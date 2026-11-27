"""Edited comments and posts compared with the archive's first copy ("view original")."""
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import app
from core import cache
from core.textdiff import word_diff, similarity
from reddit import archive
from routes import page_data

SAME_SITE = {"Sec-Fetch-Site": "same-origin"}
ROOT = Path(__file__).resolve().parent.parent


def _resp(data):
    r = MagicMock()
    r.json.return_value = {"data": data}
    return r


@pytest.fixture(autouse=True)
def _clear_cache():
    cache._fetch_cache.clear()
    cache._view_cache.clear()
    yield
    cache._fetch_cache.clear()
    cache._view_cache.clear()


# ── word_diff ──────────────────────────────────────────────────────────────────

def test_word_diff_marks_changed_words():
    assert word_diff("the quick brown fox", "the slow brown fox jumps") == [
        ["=", "the "], ["-", "quick"], ["+", "slow"], ["=", " brown fox"], ["+", " jumps"]]


def test_word_diff_identical_and_empty():
    assert word_diff("same text", "same text") == [["=", "same text"]]
    assert word_diff("", "new") == [["+", "new"]]
    assert word_diff("old", "") == [["-", "old"]]


def test_word_diff_rebuilds_both_sides():
    old = "line one\n\nEDIT: nope\nline three has words"
    new = "line one\nline two\n\nline three has more words\n\nEDIT: thanks!"
    d = word_diff(old, new)
    assert "".join(t for op, t in d if op != "+") == old
    assert "".join(t for op, t in d if op != "-") == new
    assert word_diff("same \n", "same") == [["=", "same"]]


def test_word_diff_groups_a_rewrite_into_phrases():
    d = word_diff("Nobody is getting post content props for these. Everyone knows",
                  "The original post content no longer exists here. The author used")
    assert d == [["-", "Nobody is getting"], ["+", "The original"], ["=", " post content "],
                 ["-", "props for these. Everyone knows"], ["+", "no longer exists here. The author used"]]
    assert similarity(d) < 0.5
    assert similarity(word_diff("the quick brown fox", "the slow brown fox")) > 0.5


def test_word_diff_huge_middle_falls_back_to_replace():
    old = " ".join(f"a{i}" for i in range(2000))
    new = " ".join(f"b{i}" for i in range(2000))
    assert [op for op, _ in word_diff(old, new)] == ["-", "+"]


CASES = [
    ("the quick brown fox", "the slow brown fox jumps"),
    ("a b c d e", "e d c b a"),
    ("Hello  world\n\n* one\n* two", "Hello world\n\n* one\n* two\n* three"),
    ("", "x"), ("x y", "x y"), ("  padded\n", "padded"),
    ("Nobody is getting post content props for these.", "The original post content no longer exists here."),
    ("I think so. EDIT: wrong", "I don't think so.\n\nEDIT: wrong, see below"),
]


def test_js_diff_matches_python():
    script = ("import { wordDiff, similarity } from './static/textdiff.js';"
              f"console.log(JSON.stringify({json.dumps(CASES)}.map(([a, b]) => "
              "{ const d = wordDiff(a, b); return [d, similarity(d)]; })));")
    try:
        out = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        pytest.skip("node not installed")
    expected = [[word_diff(a, b), similarity(word_diff(a, b))] for a, b in CASES]
    assert json.loads(out) == expected


# ── archive fetch and API ──────────────────────────────────────────────────────

def test_fetch_archived_original_comment():
    with patch.object(archive, "arctic_shift_get", return_value=_resp(
            [{"id": "aaa", "body": "first text", "retrieved_on": 123}])) as get:
        assert archive.fetch_archived_original("comment", "aaa") == {"body": "first text", "retrieved_utc": 123}
    assert get.call_args.args[:2] == ("/comments/ids", {"ids": "aaa"})


def test_fetch_archived_original_post_and_gone():
    with patch.object(archive, "arctic_shift_get", return_value=_resp([{"id": "p1", "selftext": "body"}])) as get:
        assert archive.fetch_archived_original("post", "p1")["body"] == "body"
    assert get.call_args.args[0] == "/posts/ids"
    with patch.object(archive, "arctic_shift_get", return_value=_resp([{"id": "p1", "selftext": "[deleted]"}])):
        assert archive.fetch_archived_original("post", "p1") is None
    with patch.object(archive, "arctic_shift_get", return_value=_resp([])):
        assert archive.fetch_archived_original("post", "p1") is None


def test_api_original():
    c = app.test_client()
    with patch.object(archive, "arctic_shift_get", return_value=_resp([{"id": "aaa", "body": "first", "retrieved_on": 5}])):
        resp = c.get("/api/archive/original/comment/aaa", headers=SAME_SITE)
    assert resp.status_code == 200 and resp.get_json() == {"body": "first", "retrieved_utc": 5}
    with patch.object(archive, "arctic_shift_get", return_value=_resp([])):
        assert c.get("/api/archive/original/post/p2", headers=SAME_SITE).status_code == 404
    with patch.object(archive, "arctic_shift_get", side_effect=RuntimeError("down")):
        assert c.get("/api/archive/original/comment/bbb", headers=SAME_SITE).status_code == 502
    assert c.get("/api/archive/original/user/aaa", headers=SAME_SITE).status_code == 400
    assert c.get("/api/archive/original/comment/a.b", headers=SAME_SITE).status_code == 400


# ── no-JS page ─────────────────────────────────────────────────────────────────

def _data():
    return {"post": {"id": "p1", "title": "T", "subreddit": "x", "author": "op", "is_self": True,
                     "selftext": "post now", "edited_utc": 50},
            "comments": [{"id": "aaa", "body": "comment now", "author": "bob", "edited_utc": 60, "replies": []},
                         {"id": "bbb", "body": "plain", "author": "eve", "replies": []}],
            "avatar_prefetch": {}}


def test_noscript_edited_items_link_to_original():
    with patch.object(page_data, "fetch_comments", return_value=_data()):
        html = app.test_client().get("/r/x/comments/p1").get_data(as_text=True)
    assert 'href="/r/x/comments/p1?original=1"' in html
    assert 'href="/r/x/comments/p1/_/aaa?sort=confidence&amp;original=1"' in html
    assert "/_/bbb?sort=confidence&amp;original=1" not in html


def test_noscript_comment_original_diff():
    with patch.object(page_data, "fetch_comments", return_value=_data()), \
            patch.object(archive, "arctic_shift_get", return_value=_resp(
                [{"id": "aaa", "body": "comment then", "retrieved_on": 61}])):
        html = app.test_client().get("/r/x/comments/p1/_/aaa?original=1").get_data(as_text=True)
    assert '<div class="edit-diff-text">comment <del>then</del><ins>now</ins></div>' in html
    assert "<script>window.__INITIAL_POST__" not in html


def test_noscript_post_original_states():
    with app.test_request_context("/r/x/comments/p1?original=1"), \
            patch.object(page_data, "fetch_comments", return_value=_data()), \
            patch.object(archive, "arctic_shift_get", return_value=_resp([{"id": "p1", "selftext": "post now"}])):
        page = page_data.build_post("x", "p1", original=True)["page"]
    assert page["original"]["status"] == "same"
    cache._fetch_cache.clear()
    with app.test_request_context("/r/x/comments/p1?original=1"), \
            patch.object(page_data, "fetch_comments", return_value=_data()), \
            patch.object(archive, "arctic_shift_get", return_value=_resp([])):
        page = page_data.build_post("x", "p1", original=True)["page"]
    assert page["original"]["status"] == "missing"
    with patch.object(page_data, "fetch_comments", return_value=_data()), \
            patch.object(archive, "arctic_shift_get", return_value=_resp([])):
        html = app.test_client().get("/r/x/comments/p1?original=1").get_data(as_text=True)
    assert '<span class="restore-btn original-btn">not archived</span>' in html


def test_noscript_rewritten_comment_shows_original_whole():
    with patch.object(page_data, "fetch_comments", return_value=_data()), \
            patch.object(archive, "arctic_shift_get", return_value=_resp(
                [{"id": "aaa", "body": "something **entirely** different", "retrieved_on": 61}])):
        html = app.test_client().get("/r/x/comments/p1/_/aaa?original=1").get_data(as_text=True)
    assert "Rewritten since the archived original" in html
    assert '<div class="edit-diff-orig md"><p>something <strong>entirely</strong> different</p>' in html
    assert "edit-diff-text" not in html
