"""Deleted comments restored from the Arctic Shift archive (the "restore" button)."""
from unittest.mock import MagicMock, patch

import pytest

from app import app
from core import cache
from reddit import archive
from routes import comments as comments_routes, page_data


def _resp(data):
    r = MagicMock()
    r.json.return_value = {"data": data}
    return r


SAME_SITE = {"Sec-Fetch-Site": "same-origin"}

ARCHIVED = [
    {"id": "aaa", "author": "alice", "body": "original text"},
    {"id": "bbb", "author": "[deleted]", "body": "[deleted]"},   # archived after the deletion
    {"id": "ccc", "author": "[deleted]", "body": "kept body"},   # author was gone already
    {"id": "eee", "author": "bob", "body": "[removed]"},         # text gone, author known
]


@pytest.fixture(autouse=True)
def _clear_cache():
    cache._fetch_cache.clear()
    cache._view_cache.clear()
    yield
    cache._fetch_cache.clear()
    cache._view_cache.clear()


def test_fetch_archived_comment_bodies_skips_deleted_copies():
    with patch.object(archive, "arctic_shift_get", return_value=_resp(ARCHIVED)) as get:
        found = archive.fetch_archived_comment_bodies(["aaa", "bbb", "ccc", "eee", "aaa"])
    assert get.call_args.args[1] == {"ids": "aaa,bbb,ccc,eee"}
    assert found == {"aaa": {"author": "alice", "body": "original text"},
                     "ccc": {"author": "[deleted]", "body": "kept body"},
                     "eee": {"author": "bob", "body": ""}}


def test_api_returns_restored_comments():
    with patch.object(archive, "arctic_shift_get", return_value=_resp(ARCHIVED)):
        resp = app.test_client().get("/api/archive/comments?ids=aaa,bbb,ccc", headers=SAME_SITE)
    assert resp.status_code == 200
    data = resp.get_json()["comments"]
    assert set(data) == {"aaa", "ccc"}   # eee wasn't asked for
    assert data["aaa"]["author"] == "alice" and data["aaa"]["body"] == "original text"


def test_api_rejects_bad_ids():
    assert app.test_client().get("/api/archive/comments?ids=../x", headers=SAME_SITE).status_code == 400


def test_api_archive_down_is_502():
    with patch.object(archive, "arctic_shift_get", side_effect=RuntimeError("down")):
        resp = app.test_client().get("/api/archive/comments?ids=aaa", headers=SAME_SITE)
    assert resp.status_code == 502


def test_restore_deleted_copies_tree():
    tree = [{"id": "aaa", "body": "[deleted]", "author": "[deleted]", "replies": [
        {"id": "ddd", "body": "[removed]", "author": "[deleted]", "replies": []},
        {"kind": "more", "id": "m", "children": ["x"]}]}]
    out = comments_routes.restore_deleted(tree, {"aaa": {"author": "alice", "body": "hi"}})
    assert out[0]["body"] == "hi" and out[0]["author"] == "alice" and out[0]["restored"]
    assert out[0]["replies"][0]["body"] == "[removed]"
    assert tree[0]["body"] == "[deleted]"   # the cached original is untouched


def test_restore_deleted_author_keeps_body():
    tree = [{"id": "aaa", "body": "still here", "author": "[deleted]", "replies": []},
            {"id": "eee", "body": "[removed]", "author": "[deleted]", "replies": []},
            {"id": "fff", "body": "fine", "author": "[deleted]", "replies": []}]
    out = comments_routes.restore_deleted(tree, {
        "aaa": {"author": "alice", "body": "archived copy"},
        "eee": {"author": "bob", "body": ""},
        "fff": {"author": "[deleted]", "body": "fine"}})
    assert out[0]["author"] == "alice" and out[0]["body"] == "still here" and out[0]["restored"]
    assert out[1]["author"] == "bob" and out[1]["body"] == "[removed]" and out[1]["restored"]
    assert out[2]["author"] == "[deleted]" and not out[2].get("restored")


def _comments_data():
    return {"post": {"id": "p1", "title": "T", "subreddit": "x", "author": "op"},
            "comments": [{"id": "aaa", "body": "[deleted]", "author": "[deleted]", "replies": []}],
            "avatar_prefetch": {}}


def test_noscript_comment_page_restores_that_comment():
    with app.test_request_context("/r/x/comments/p1/_/aaa?restore=1"), \
            patch.object(page_data, "fetch_comments", return_value=_comments_data()), \
            patch.object(archive, "arctic_shift_get", return_value=_resp(ARCHIVED)):
        page = page_data.build_post("x", "p1", "aaa", restore=True)["page"]
    c = page["comments"][0]
    assert c["body"] == "original text" and c["restored"]
    assert page["restore_id"] == "aaa"


def test_noscript_restore_needs_a_comment():
    with app.test_request_context("/r/x/comments/p1?restore=1"), \
            patch.object(page_data, "fetch_comments", return_value=_comments_data()), \
            patch.object(archive, "arctic_shift_get") as get:
        page = page_data.build_post("x", "p1", restore=True)["page"]
    get.assert_not_called()
    assert page["comments"][0]["body"] == "[deleted]" and not page["restore_id"]


def test_noscript_deleted_comment_links_to_restored_page():
    with patch.object(page_data, "fetch_comments", return_value=_comments_data()):
        html = app.test_client().get("/r/x/comments/p1").get_data(as_text=True)
    assert 'href="/r/x/comments/p1/_/aaa?sort=confidence&amp;restore=1"' in html


def test_noscript_deleted_author_gets_restore_link():
    data = _comments_data()
    data["comments"][0].update(body="kept", author="[deleted]")
    with patch.object(page_data, "fetch_comments", return_value=data):
        html = app.test_client().get("/r/x/comments/p1").get_data(as_text=True)
    assert 'href="/r/x/comments/p1/_/aaa?sort=confidence&amp;restore=1"' in html
