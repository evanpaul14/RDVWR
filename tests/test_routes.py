"""Integration tests for the Flask routes (app.py + routes/).

All external HTTP calls (SESSION.get / SESSION.head) are mocked so no network
traffic is made during the test run.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import pytest
from unittest.mock import patch, MagicMock

import helpers
import reddit_client
from app import app
from routes import media as media_routes, embeds as embeds_routes


# ── Mock helpers ──────────────────────────────────────────────────────────────

class MockResponse:
    def __init__(self, data=None, status_code=200, headers=None, raw_bytes=b""):
        self._data = data
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}
        self.ok = status_code < 400
        self._raw = raw_bytes
        self.url = "https://mocked.example.com/"
        self.text = ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size=None):
        yield self._raw

    def close(self):
        pass


def _make_listing(posts=None, after=None):
    """Build a Reddit listing envelope."""
    children = []
    for p in (posts or []):
        children.append({"kind": "t3", "data": p})
    return {"data": {"children": children, "after": after}}


def _make_post(post_id="abc123", title="Test", subreddit="testsubreddit"):
    return {"id": post_id, "title": title, "subreddit": subreddit}


def _session_get(data=None, status_code=200, **kw):
    return MockResponse(data=data, status_code=status_code, **kw)


@pytest.fixture(autouse=True)
def no_oauth(monkeypatch):
    """Force reddit_get to use SESSION (not cffi) so SESSION.get mocks work."""
    monkeypatch.setattr(reddit_client, "REDDIT_OAUTH", False)
    helpers._view_cache.clear()


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        # Requests below exercise route behavior, not the same-site gate in
        # app.py's _gate_api — mark them as same-origin so that gate passes.
        c.environ_base = {"HTTP_SEC_FETCH_SITE": "same-origin"}
        yield c


class TestApiSameSiteGate:
    """The `client` fixture sets Sec-Fetch-Site: same-origin by default so the other
    ~200 tests can exercise route behavior without tripping this gate. These tests
    exercise helpers.is_same_site_request directly (no live route/network involved)
    plus a couple of end-to-end checks of app.py's _gate_api wiring."""

    def test_no_headers_rejected(self):
        with app.test_request_context("/api/home"):
            assert helpers.is_same_site_request() is False

    def test_sec_fetch_site_same_origin_allowed(self):
        with app.test_request_context("/api/home", headers={"Sec-Fetch-Site": "same-origin"}):
            assert helpers.is_same_site_request() is True

    def test_sec_fetch_site_same_site_allowed(self):
        with app.test_request_context("/api/home", headers={"Sec-Fetch-Site": "same-site"}):
            assert helpers.is_same_site_request() is True

    def test_sec_fetch_site_cross_site_rejected(self):
        with app.test_request_context("/api/home", headers={"Sec-Fetch-Site": "cross-site"}):
            assert helpers.is_same_site_request() is False

    def test_origin_match_allowed(self):
        with app.test_request_context("/api/home", headers={"Origin": "http://localhost"}):
            assert helpers.is_same_site_request() is True

    def test_origin_mismatch_rejected(self):
        with app.test_request_context("/api/home", headers={"Origin": "https://evil.example.com"}):
            assert helpers.is_same_site_request() is False

    def test_referer_match_allowed_when_no_origin(self):
        with app.test_request_context("/api/home", headers={"Referer": "http://localhost/r/python"}):
            assert helpers.is_same_site_request() is True

    def test_referer_mismatch_rejected(self):
        with app.test_request_context("/api/home", headers={"Referer": "https://evil.example.com/"}):
            assert helpers.is_same_site_request() is False

    def test_sec_fetch_site_takes_priority_over_mismatched_origin(self):
        # A browser sending both is not the normal case (Sec-Fetch-Site itself would
        # be cross-site if Origin were an attacker's), but Sec-Fetch-Site is checked
        # first and is the more trustworthy signal when both are present.
        with app.test_request_context("/api/home", headers={
            "Sec-Fetch-Site": "same-origin", "Origin": "https://evil.example.com",
        }):
            assert helpers.is_same_site_request() is True

    def test_end_to_end_rejects_bare_curl_style_request(self):
        with app.test_client() as c:
            resp = c.get("/api/home")
        assert resp.status_code == 403

    def test_end_to_end_non_api_path_unaffected(self):
        with app.test_client() as c:
            resp = c.get("/")
        assert resp.status_code == 200


# ── SPA catch-all ─────────────────────────────────────────────────────────────

class TestSPARoutes:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_subreddit_path(self, client):
        resp = client.get("/r/python")
        assert resp.status_code == 200

    def test_post_path(self, client):
        resp = client.get("/r/python/comments/abc123/title/")
        assert resp.status_code == 200

    def test_user_path(self, client):
        resp = client.get("/user/someguy")
        assert resp.status_code == 200

    def test_search_path(self, client):
        resp = client.get("/search")
        assert resp.status_code == 200

    def test_spa_no_cache(self, client):
        resp = client.get("/")
        assert "no-store" in resp.headers.get("Cache-Control", "")


# ── /api/r/<sub> (feed) ───────────────────────────────────────────────────────

class TestSubredditFeed:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get(_make_listing([_make_post()]))
        resp = client.get("/api/r/python")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "posts" in data
        assert len(data["posts"]) == 1

    @patch.object(reddit_client.SESSION, "get")
    def test_combined_feed_plus_is_percent_encoded(self, mock_get, client):
        mock_get.return_value = _session_get(_make_listing([_make_post()]))
        resp = client.get("/api/r/pics+aww?sort=hot")
        assert resp.status_code == 200
        assert mock_get.call_args.args[0] == "https://www.reddit.com/r/pics%2Baww/hot.json"

    @patch.object(reddit_client.SESSION, "get")
    def test_404_from_reddit(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=404)
        resp = client.get("/api/r/nonexistent_sub_xyz")
        assert resp.status_code == 404

    @patch.object(reddit_client.SESSION, "get")
    def test_403_private_subreddit(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=403)
        resp = client.get("/api/r/private_sub")
        assert resp.status_code == 403

    @patch.object(reddit_client.SESSION, "get")
    def test_after_param_forwarded(self, mock_get, client):
        mock_get.return_value = _session_get(_make_listing([], after="t3_next"))
        resp = client.get("/api/r/python?after=t3_abc")
        assert resp.status_code == 200
        call_kwargs = mock_get.call_args
        assert "after" in str(call_kwargs)

    @patch.object(reddit_client.SESSION, "get")
    def test_timeout_returns_504(self, mock_get, client):
        import requests as req_lib
        mock_get.side_effect = req_lib.exceptions.Timeout()
        resp = client.get("/api/r/python")
        assert resp.status_code == 504


# ── /api/r/<sub>/about ────────────────────────────────────────────────────────

class TestSubredditAbout:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({
            "data": {
                "title": "Python",
                "public_description": "desc",
                "description": "sidebar",
                "subscribers": 1000000,
                "active_user_count": 5000,
                "icon_img": "",
                "community_icon": "",
            }
        })
        resp = client.get("/api/r/python/about")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Python"
        assert data["subscribers"] == 1000000

    @patch.object(reddit_client.SESSION, "get")
    def test_error_from_reddit(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=404)
        resp = client.get("/api/r/gone/about")
        assert resp.status_code == 404


# ── /api/r/<sub>/rules ────────────────────────────────────────────────────────

class TestSubredditRules:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({
            "rules": [{"short_name": "Rule 1", "description": "Be nice"}]
        })
        resp = client.get("/api/r/python/rules")
        assert resp.status_code == 200
        assert len(resp.get_json()["rules"]) == 1

    @patch.object(reddit_client.SESSION, "get")
    def test_error_returns_empty_rules(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=500)
        resp = client.get("/api/r/python/rules")
        assert resp.status_code == 200
        assert resp.get_json()["rules"] == []


# ── /api/r/<sub>/comments/<id> ────────────────────────────────────────────────

class TestComments:
    def _comments_response(self, post_extra=None):
        post = {**_make_post(), "permalink": "/r/testsubreddit/comments/abc123/title/"}
        if post_extra:
            post.update(post_extra)
        return [
            {"data": {"children": [{"kind": "t3", "data": post}]}},
            {"data": {"children": []}},
        ]

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get(self._comments_response())
        resp = client.get("/api/r/testsubreddit/comments/abc123")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "post" in data
        assert "comments" in data

    @patch.object(reddit_client.SESSION, "get")
    def test_invalid_sort_defaults_to_confidence(self, mock_get, client):
        mock_get.return_value = _session_get(self._comments_response())
        resp = client.get("/api/r/testsubreddit/comments/abc123?sort=bogus")
        assert resp.status_code == 200
        url_called = mock_get.call_args[0][0]
        # sort param defaults — check 'confidence' was used in the params dict
        params_used = mock_get.call_args[1].get("params", {})
        assert params_used.get("sort") == "confidence"

    @patch.object(reddit_client.SESSION, "get")
    def test_valid_sort_passed_through(self, mock_get, client):
        mock_get.return_value = _session_get(self._comments_response())
        resp = client.get("/api/r/testsubreddit/comments/abc123?sort=top")
        params_used = mock_get.call_args[1].get("params", {})
        assert params_used.get("sort") == "top"

    @patch.object(reddit_client.SESSION, "get")
    def test_error_from_reddit(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=404)
        resp = client.get("/api/r/testsubreddit/comments/abc123")
        assert resp.status_code == 404


# ── /api/r/<sub>/morechildren/<id> ───────────────────────────────────────────

class TestMoreChildren:
    @patch.object(reddit_client.SESSION, "get")
    def test_no_children_param_returns_empty(self, mock_get, client):
        resp = client.get("/api/r/testsubreddit/morechildren/abc123")
        assert resp.status_code == 200
        assert resp.get_json()["comments"] == []
        mock_get.assert_not_called()

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({
            "json": {"data": {"things": [
                {"kind": "t1", "data": {
                    "id": "c1", "author": "user", "body": "hello", "score": 5,
                    "created_utc": 0, "edited": False, "depth": 1,
                    "parent_id": "t3_abc123", "distinguished": None,
                    "stickied": False, "author_flair_text": None,
                    "author_flair_richtext": [], "author_flair_type": "text",
                    "author_flair_background_color": None,
                    "author_flair_text_color": None, "all_awardings": [],
                }}
            ]}}
        })
        resp = client.get("/api/r/testsubreddit/morechildren/abc123?children=c1")
        assert resp.status_code == 200
        assert len(resp.get_json()["comments"]) == 1


# ── /api/search ───────────────────────────────────────────────────────────────

class TestSearch:
    @patch.object(reddit_client.SESSION, "get")
    def test_missing_query_returns_400(self, mock_get, client):
        resp = client.get("/api/search")
        assert resp.status_code == 400

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get(_make_listing([_make_post()]))
        resp = client.get("/api/search?q=python")
        assert resp.status_code == 200
        assert "posts" in resp.get_json()

    @patch.object(reddit_client.SESSION, "get")
    def test_invalid_sort_defaults_to_relevance(self, mock_get, client):
        mock_get.return_value = _session_get(_make_listing([]))
        resp = client.get("/api/search?q=test&sort=bogus")
        params_used = mock_get.call_args[1].get("params", {})
        assert params_used.get("sort") == "relevance"

    @patch.object(reddit_client.SESSION, "get")
    def test_sub_restricts_search(self, mock_get, client):
        mock_get.return_value = _session_get(_make_listing([]))
        resp = client.get("/api/search?q=test&sub=python")
        url_called = mock_get.call_args[0][0]
        assert "/r/python/" in url_called

    @patch.object(reddit_client.SESSION, "get")
    def test_timeout_returns_504(self, mock_get, client):
        import requests as req_lib
        mock_get.side_effect = req_lib.exceptions.Timeout()
        resp = client.get("/api/search?q=test")
        assert resp.status_code == 504


# ── /api/search/communities ───────────────────────────────────────────────────

class TestSearchCommunities:
    def test_empty_query_returns_empty(self, client):
        resp = client.get("/api/search/communities")
        assert resp.status_code == 200
        assert resp.get_json()["communities"] == []

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({"data": {"children": [
            {"kind": "t5", "data": {
                "display_name": "python", "title": "Python",
                "public_description": "A subreddit", "subscribers": 500000,
                "over_18": False, "icon_img": "", "community_icon": "",
            }}
        ], "after": None}})
        resp = client.get("/api/search/communities?q=python")
        data = resp.get_json()
        assert len(data["communities"]) == 1
        assert data["communities"][0]["name"] == "python"


# ── /api/search/users ────────────────────────────────────────────────────────

class TestSearchUsers:
    def test_empty_query_returns_empty(self, client):
        resp = client.get("/api/search/users")
        assert resp.status_code == 200
        assert resp.get_json()["users"] == []

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({"data": {"children": [
            {"kind": "t2", "data": {
                "name": "testuser", "icon_img": "", "snoovatar_img": "",
                "link_karma": 100, "comment_karma": 200, "created_utc": 0,
            }}
        ], "after": None}})
        resp = client.get("/api/search/users?q=testuser")
        data = resp.get_json()
        assert data["users"][0]["name"] == "testuser"


# ── /api/subreddit-search ─────────────────────────────────────────────────────

def _autocomplete_listing(names):
    return {"data": {"children": [
        {"kind": "t5", "data": {"display_name": n, "icon_img": "", "subscribers": 100, "over18": False}}
        for n in names
    ]}}


class TestSubredditSearch:
    def test_short_query_returns_empty(self, client):
        resp = client.get("/api/subreddit-search?q=p")
        assert resp.status_code == 200
        assert resp.get_json()["subs"] == []

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get(_autocomplete_listing(["python", "pythonista"]))
        resp = client.get("/api/subreddit-search?q=py")
        names = [s["name"] for s in resp.get_json()["subs"]]
        assert names == ["python", "pythonista"]

    @patch.object(reddit_client.SESSION, "get")
    def test_capped_at_eight(self, mock_get, client):
        mock_get.return_value = _session_get(_autocomplete_listing([f"sub{i}" for i in range(20)]))
        resp = client.get("/api/subreddit-search?q=sub")
        assert len(resp.get_json()["subs"]) <= 8


# ── /api/user/<username>/about ───────────────────────────────────────────────

class TestUserAbout:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({"data": {
            "name": "testuser", "icon_img": "", "snoovatar_img": "",
            "link_karma": 1000, "comment_karma": 5000,
            "created_utc": 1600000000, "is_gold": False,
        }})
        resp = client.get("/api/user/testuser/about")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "testuser"
        assert "karma_post" in data

    @patch.object(reddit_client.SESSION, "get")
    def test_404(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=404)
        resp = client.get("/api/user/doesnotexist_xyzabc/about")
        assert resp.status_code == 404


# ── /api/user/<username>/posts ───────────────────────────────────────────────

class TestUserPosts:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get(_make_listing([_make_post()]))
        resp = client.get("/api/user/testuser/posts")
        assert resp.status_code == 200
        assert "posts" in resp.get_json()

    @patch.object(reddit_client.SESSION, "get")
    def test_404(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=404)
        resp = client.get("/api/user/gone/posts")
        assert resp.status_code == 404

    @patch.object(reddit_client.SESSION, "get")
    def test_falls_back_to_arctic_shift_on_404(self, mock_get, client):
        def side_effect(url, **kw):
            if "arctic-shift" in url:
                return _session_get({"data": [_make_post(post_id="archived1")]})
            return _session_get(status_code=404)
        mock_get.side_effect = side_effect
        resp = client.get("/api/user/suspendeduser/posts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["archived"] is True
        assert data["posts"][0]["id"] == "archived1"

    @patch.object(reddit_client.SESSION, "get")
    def test_404_when_arctic_shift_also_empty(self, mock_get, client):
        def side_effect(url, **kw):
            if "arctic-shift" in url:
                return _session_get({"data": []})
            return _session_get(status_code=404)
        mock_get.side_effect = side_effect
        resp = client.get("/api/user/gone/posts")
        assert resp.status_code == 404


# ── /api/user/<username>/comments ────────────────────────────────────────────

class TestUserComments:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({"data": {"children": [
            {"kind": "t1", "data": {
                "id": "c1", "author": "testuser", "body": "A comment",
                "score": 10, "created_utc": 0, "subreddit": "python",
                "link_title": "A post", "link_permalink": "/r/python/comments/xyz/",
                "link_id": "t3_xyz",
            }}
        ], "after": None}})
        resp = client.get("/api/user/testuser/comments")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["comments"]) == 1
        assert data["comments"][0]["body"] == "A comment"

    @patch.object(reddit_client.SESSION, "get")
    def test_link_permalink_prefixed(self, mock_get, client):
        mock_get.return_value = _session_get({"data": {"children": [
            {"kind": "t1", "data": {
                "id": "c2", "author": "u", "body": "b", "score": 0,
                "created_utc": 0, "subreddit": "s",
                "link_title": "t", "link_permalink": "/r/s/comments/1/",
                "link_id": "t3_1",
            }}
        ], "after": None}})
        resp = client.get("/api/user/testuser/comments")
        comment = resp.get_json()["comments"][0]
        assert comment["link_permalink"].startswith("https://www.reddit.com")

    @patch.object(reddit_client.SESSION, "get")
    def test_falls_back_to_arctic_shift_with_title_backfill(self, mock_get, client):
        def side_effect(url, **kw):
            if url.endswith("/comments/search"):
                return _session_get({"data": [{
                    "id": "c1", "author": "suspendeduser", "body": "archived comment",
                    "score": 3, "created_utc": 0, "subreddit": "python", "link_id": "t3_xyz",
                }]})
            if url.endswith("/posts/ids"):
                return _session_get({"data": [{
                    "id": "xyz", "title": "Archived Post Title", "permalink": "/r/python/comments/xyz/",
                }]})
            return _session_get(status_code=404)
        mock_get.side_effect = side_effect
        resp = client.get("/api/user/suspendeduser/comments")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["archived"] is True
        comment = data["comments"][0]
        assert comment["body"] == "archived comment"
        assert comment["link_title"] == "Archived Post Title"
        assert comment["link_permalink"] == "https://www.reddit.com/r/python/comments/xyz/"


# ── /api/user/<username>/overview ────────────────────────────────────────────

class TestUserOverview:
    @patch.object(reddit_client.SESSION, "get")
    def test_mixed_post_and_comment(self, mock_get, client):
        mock_get.return_value = _session_get({"data": {"children": [
            {"kind": "t3", "data": _make_post()},
            {"kind": "t1", "data": {
                "id": "c1", "author": "u", "body": "hello", "score": 1,
                "created_utc": 0, "subreddit": "s", "link_title": "t",
                "link_permalink": "https://www.reddit.com/r/s/comments/1/",
                "link_id": "t3_1",
            }},
        ], "after": None}})
        resp = client.get("/api/user/testuser/overview")
        data = resp.get_json()
        types = [item["type"] for item in data["items"]]
        assert "post" in types
        assert "comment" in types


# ── /api/user/<username>/m/<multiname> ───────────────────────────────────────

class TestMultireddit:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        meta_resp = MockResponse({"data": {"subreddits": [{"name": "python"}], "display_name": "MyMulti"}})
        feed_resp = MockResponse(_make_listing([_make_post()]))
        mock_get.side_effect = [meta_resp, feed_resp]
        resp = client.get("/api/user/testuser/m/mymulti")
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "MyMulti"

    @patch.object(reddit_client.SESSION, "get")
    def test_empty_subs_returns_no_posts(self, mock_get, client):
        meta_resp = MockResponse({"data": {"subreddits": [], "display_name": "empty"}})
        mock_get.return_value = meta_resp
        resp = client.get("/api/user/testuser/m/empty")
        assert resp.status_code == 200
        assert resp.get_json()["posts"] == []

    @patch.object(reddit_client.SESSION, "get")
    def test_404(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=404)
        resp = client.get("/api/user/testuser/m/notfound")
        assert resp.status_code == 404


# ── /api/r/<sub>/wiki ────────────────────────────────────────────────────────

class TestWiki:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({"data": {
            "content_html": "&lt;p&gt;Hello&lt;/p&gt;",
            "revision_date": 1700000000,
        }})
        resp = client.get("/api/r/python/wiki/index")
        assert resp.status_code == 200
        assert "content_html" in resp.get_json()

    def test_invalid_page_name(self, client):
        resp = client.get("/api/r/python/wiki/%00evil")
        assert resp.status_code in (400, 404)

    @patch.object(reddit_client.SESSION, "get")
    def test_private_wiki_403(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=403)
        resp = client.get("/api/r/private/wiki/index")
        assert resp.status_code == 403

    @patch.object(reddit_client.SESSION, "get")
    def test_sc_off_on_stripped(self, mock_get, client):
        mock_get.return_value = _session_get({"data": {
            "content_html": "<!-- SC_OFF -->&lt;p&gt;text&lt;/p&gt;<!-- SC_ON -->",
            "revision_date": None,
        }})
        resp = client.get("/api/r/python/wiki/index")
        html = resp.get_json()["content_html"]
        assert "SC_OFF" not in html
        assert "SC_ON" not in html


# ── /api/r/<sub>/duplicates/<id> ─────────────────────────────────────────────

class TestDuplicates:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        orig = _make_post(post_id="abc123")
        orig["selftext"] = "some text"
        mock_get.return_value = _session_get([
            {"data": {"children": [{"kind": "t3", "data": orig}]}},
            {"data": {"children": [{"kind": "t3", "data": _make_post(post_id="dup1")}], "after": None}},
        ])
        resp = client.get("/api/r/testsubreddit/duplicates/abc123")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["post"]["id"] == "abc123"
        assert len(data["posts"]) == 1


# ── /api/live/<thread_id> ─────────────────────────────────────────────────────

class TestLiveThread:
    def _make_live_listing(self, updates=None):
        children = []
        for u in (updates or []):
            children.append({"kind": "LiveUpdate", "data": u})
        return {"data": {"children": children, "after": None}}

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        info = MockResponse({"data": {
            "title": "Live Event", "description": "desc",
            "state": "live", "viewer_count": 1234,
        }})
        updates = MockResponse(self._make_live_listing([{
            "id": "u1", "body": "update", "author": "reporter",
            "created_utc": 1700000000, "stricken": False,
        }]))
        mock_get.side_effect = [info, updates]
        resp = client.get("/api/live/abc123thread")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Live Event"
        assert len(data["updates"]) == 1

    def test_invalid_thread_id(self, client):
        resp = client.get("/api/live/../../etc/passwd")
        # Flask will normalize the URL before it reaches our handler,
        # but our regex check should reject any non-alphanumeric IDs
        assert resp.status_code in (400, 404)

    @patch.object(reddit_client.SESSION, "get")
    def test_404(self, mock_get, client):
        mock_get.return_value = _session_get(status_code=404)
        resp = client.get("/api/live/notfound123")
        assert resp.status_code == 404


# ── /api/live/<thread_id>/updates ────────────────────────────────────────────

class TestLiveUpdates:
    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({
            "data": {"children": [], "after": None}
        })
        resp = client.get("/api/live/abc123/updates")
        assert resp.status_code == 200
        assert "updates" in resp.get_json()

    def test_invalid_id_rejected(self, client):
        resp = client.get("/api/live/<script>/updates")
        assert resp.status_code in (400, 404)


# ── /api/img (image proxy) ────────────────────────────────────────────────────

class TestImgProxy:
    @patch.object(reddit_client.SESSION, "get")
    def test_allowed_host(self, mock_get, client):
        mock_get.return_value = MockResponse(
            status_code=200,
            headers={"Content-Type": "image/jpeg"},
            raw_bytes=b"\xff\xd8\xff",
        )
        resp = client.get("/api/img?url=https://preview.redd.it/img.jpg")
        assert resp.status_code == 200

    def test_disallowed_host_rejected(self, client):
        resp = client.get("/api/img?url=https://evil.com/img.jpg")
        assert resp.status_code == 403

    def test_non_http_scheme_rejected(self, client):
        resp = client.get("/api/img?url=file:///etc/passwd")
        assert resp.status_code == 403

    def test_empty_url_rejected(self, client):
        resp = client.get("/api/img?url=")
        assert resp.status_code == 403

    @patch.object(reddit_client.SESSION, "get")
    def test_external_preview_host_allowed(self, mock_get, client):
        mock_get.return_value = MockResponse(
            status_code=200,
            headers={"Content-Type": "image/jpeg"},
            raw_bytes=b"data",
        )
        resp = client.get("/api/img?url=https://external-preview.redd.it/img.jpg")
        assert resp.status_code == 200


# ── /api/m/<host>/<path> (opt-in media proxy) ─────────────────────────────────

@pytest.fixture
def proxy_media_on():
    app.config["PROXY_MEDIA"] = True
    yield
    app.config["PROXY_MEDIA"] = False


class TestMediaProxy:
    @patch.object(reddit_client.SESSION, "get")
    def test_allowed_host_streams_and_forwards_range(self, mock_get, client):
        mock_get.return_value = MockResponse(
            status_code=206,
            headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-2/10", "Content-Length": "3"},
            raw_bytes=b"abc",
        )
        resp = client.get("/api/m/v.redd.it/xyz/CMAF_720.mp4?a=1", headers={"Range": "bytes=0-2"})
        assert resp.status_code == 206
        assert resp.data == b"abc"
        assert resp.headers["Content-Range"] == "bytes 0-2/10"
        assert mock_get.call_args.args[0] == "https://v.redd.it/xyz/CMAF_720.mp4?a=1"
        assert mock_get.call_args.kwargs["headers"]["Range"] == "bytes=0-2"
        assert mock_get.call_args.kwargs["allow_redirects"] is False

    def test_disallowed_host_rejected(self, client):
        assert client.get("/api/m/evil.com/x.jpg").status_code == 403

    @patch.object(reddit_client.SESSION, "get")
    def test_redirect_off_allowlist_not_followed(self, mock_get, client):
        mock_get.return_value = MockResponse(status_code=302, headers={"Location": "https://evil.com/x"})
        assert client.get("/api/m/i.redd.it/x.jpg").status_code == 502
        assert mock_get.call_count == 1

    @patch.object(reddit_client.SESSION, "get")
    def test_playlist_absolute_urls_rewritten(self, mock_get, client):
        r = MockResponse(status_code=200, headers={"Content-Type": "application/vnd.apple.mpegurl"})
        r.text = "#EXTM3U\nHLS_720.m3u8\nhttps://v.redd.it/xyz/HLS_AUDIO.m3u8\n"
        mock_get.return_value = r
        body = client.get("/api/m/v.redd.it/xyz/HLSPlaylist.m3u8").get_data(as_text=True)
        assert "HLS_720.m3u8" in body
        assert "/api/m/v.redd.it/xyz/HLS_AUDIO.m3u8" in body
        assert "https://v.redd.it" not in body

    @patch.object(reddit_client.SESSION, "get")
    def test_api_urls_rewritten_when_enabled(self, mock_get, client, proxy_media_on):
        post = {**_make_post(), "url": "https://i.redd.it/pic.jpg", "domain": "i.redd.it",
                "selftext": "see https://i.redd.it/other.jpg"}
        mock_get.return_value = _session_get(_make_listing([post]))
        p = client.get("/api/r/python").get_json()["posts"][0]
        assert p["url"] == "/api/m/i.redd.it/pic.jpg"
        assert p["domain"] == "i.redd.it"

    def test_text_fields_and_arrays(self, client, proxy_media_on):
        from routes import mediaproxy
        body = (b'{"body":"https://preview.redd.it/a.png","selftext": "https://i.redd.it/b.jpg",'
                b'"url":"https://i.redd.it/c.jpg","urls":["https://i.imgur.com/d.png","https://i.redd.it/e.jpg"],'
                b'"content_html":"<img src=\\"https://i.redd.it/f.png\\">"}')
        out = mediaproxy._BODY_URL_RE.sub(mediaproxy._rewrite_match, body)
        d = json.loads(out)
        assert d["body"] == "https://preview.redd.it/a.png"
        assert d["selftext"] == "https://i.redd.it/b.jpg"
        assert d["url"] == "/api/m/i.redd.it/c.jpg"
        assert d["urls"] == ["/api/m/i.imgur.com/d.png", "/api/m/i.redd.it/e.jpg"]
        assert d["content_html"] == '<img src="/api/m/i.redd.it/f.png">'

    @patch.object(reddit_client.SESSION, "get")
    def test_api_urls_untouched_when_disabled(self, mock_get, client):
        post = {**_make_post(), "url": "https://i.redd.it/pic.jpg"}
        mock_get.return_value = _session_get(_make_listing([post]))
        assert client.get("/api/r/python").get_json()["posts"][0]["url"] == "https://i.redd.it/pic.jpg"

    def test_spa_meta_flag(self, client, proxy_media_on):
        html = client.get("/search").get_data(as_text=True)
        assert 'name="rdvwr-proxy-media"' in html
        assert 'name="referrer" content="no-referrer"' in html
        assert "fonts.googleapis.com" not in html


# ── /api/resolve ──────────────────────────────────────────────────────────────

class TestResolve:
    @patch("reddit_client.SESSION.head")
    def test_valid_reddit_url(self, mock_head, client):
        mock_resp = MagicMock()
        mock_resp.url = "https://www.reddit.com/r/python/comments/abc123/title/"
        mock_head.return_value = mock_resp
        resp = client.get("/api/resolve?url=https://reddit.com/r/python")
        assert resp.status_code == 200
        assert "url" in resp.get_json()

    @patch("reddit_client.SESSION.head")
    def test_stops_at_off_reddit_redirect(self, mock_head, client):
        mock_head.side_effect = [
            MockResponse(status_code=302, headers={"Location": "/r/python/comments/abc/"}),
            MockResponse(status_code=301, headers={"Location": "http://192.168.1.1/admin"}),
        ]
        resp = client.get("/api/resolve?url=https://reddit.com/r/python/s/xyz")
        assert resp.get_json()["url"] == "http://192.168.1.1/admin"
        assert mock_head.call_count == 2
        assert all(c.kwargs["allow_redirects"] is False for c in mock_head.call_args_list)

    def test_non_reddit_url_rejected(self, client):
        resp = client.get("/api/resolve?url=https://evil.com/redirect")
        assert resp.status_code == 400

    def test_empty_url_rejected(self, client):
        resp = client.get("/api/resolve?url=")
        assert resp.status_code == 400


# ── /api/download ────────────────────────────────────────────────────────────

class TestDownload:
    @patch.object(reddit_client.SESSION, "get")
    def test_allowed_host(self, mock_get, client):
        mock_get.return_value = MockResponse(
            status_code=200,
            headers={"Content-Type": "video/mp4", "Content-Length": "3"},
            raw_bytes=b"mp4",
        )
        resp = client.get("/api/download?url=https://v.redd.it/abc/DASH_720.mp4&filename=test.mp4")
        assert resp.status_code == 200

    def test_disallowed_host_rejected(self, client):
        resp = client.get("/api/download?url=https://evil.com/file.mp4")
        assert resp.status_code == 400

    def test_empty_url_rejected(self, client):
        resp = client.get("/api/download?url=")
        assert resp.status_code == 400


# ── /api/download/reddit-video ────────────────────────────────────────────────

class TestDownloadRedditVideo:
    def test_non_v_redd_it_rejected(self, client):
        resp = client.get("/api/download/reddit-video?hls=https://evil.com/hls.m3u8")
        assert resp.status_code == 400

    def test_empty_hls_rejected(self, client):
        resp = client.get("/api/download/reddit-video?hls=")
        assert resp.status_code == 400


# ── /api/redgifs/<gif_id> ────────────────────────────────────────────────────

class TestRedgifs:
    def test_invalid_id_rejected(self, client):
        resp = client.get("/api/redgifs/../../etc/passwd")
        assert resp.status_code in (400, 404)

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        token_resp = MockResponse({"token": "fake_token"})
        gif_resp = MockResponse({"gif": {"urls": {"hd": "https://media.redgifs.com/Test.mp4", "sd": None}}})
        mock_get.side_effect = [token_resp, gif_resp]
        resp = client.get("/api/redgifs/TestGif123")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["hd"] is not None
        assert "/api/redgifs/media/" in data["hd"]

    @patch.object(reddit_client.SESSION, "get")
    def test_proxied_url_returned(self, mock_get, client):
        # Reset cached token so mock is called
        media_routes._rg_token = "cached_token"
        media_routes._rg_token_exp = float("inf")
        gif_resp = MockResponse({"gif": {"urls": {"hd": "https://media.redgifs.com/MyGif-mobile.mp4", "sd": None}}})
        mock_get.return_value = gif_resp
        resp = client.get("/api/redgifs/MyGif123")
        assert resp.status_code == 200
        assert resp.get_json()["hd"] == "/api/redgifs/media/MyGif-mobile.mp4"

    @patch.object(reddit_client.SESSION, "get")
    def test_404_from_redgifs(self, mock_get, client):
        media_routes._rg_token = "cached_token"
        media_routes._rg_token_exp = float("inf")
        mock_get.return_value = _session_get(status_code=404)
        resp = client.get("/api/redgifs/NotFoundGif")
        assert resp.status_code == 404


    @patch.object(reddit_client.SESSION, "get")
    def test_401_refreshes_token_and_retries(self, mock_get, client):
        media_routes._rg_token = "revoked_token"
        media_routes._rg_token_exp = float("inf")
        gif_resp = MockResponse({"gif": {"urls": {"hd": "https://media.redgifs.com/Ok.mp4", "sd": None}}})
        mock_get.side_effect = [_session_get(status_code=401), MockResponse({"token": "new_token"}), gif_resp]
        resp = client.get("/api/redgifs/RetryGif")
        assert resp.status_code == 200
        assert media_routes._rg_token == "new_token"
        assert mock_get.call_args_list[2].kwargs["headers"]["Authorization"] == "Bearer new_token"

# ── /api/redgifs/media/<filename> ────────────────────────────────────────────

class TestRedgifsMedia:
    def test_invalid_filename_rejected(self, client):
        resp = client.get("/api/redgifs/media/../../etc/passwd")
        assert resp.status_code in (400, 404)

    def test_invalid_extension_rejected(self, client):
        resp = client.get("/api/redgifs/media/malicious.exe")
        assert resp.status_code == 400

    @patch.object(reddit_client.SESSION, "get")
    def test_valid_filename_proxied(self, mock_get, client):
        mock_get.return_value = MockResponse(
            status_code=200,
            headers={"Content-Type": "video/mp4"},
            raw_bytes=b"video_data",
        )
        resp = client.get("/api/redgifs/media/TestGif123.mp4")
        assert resp.status_code == 200

    @patch.object(reddit_client.SESSION, "get")
    def test_mobile_variant_valid(self, mock_get, client):
        mock_get.return_value = MockResponse(
            status_code=200,
            headers={"Content-Type": "video/mp4"},
            raw_bytes=b"data",
        )
        resp = client.get("/api/redgifs/media/TestGif123-mobile.mp4")
        assert resp.status_code == 200


# ── /api/imgur/album/<album_id> ───────────────────────────────────────────────

class TestImgurAlbum:
    def test_invalid_id_rejected(self, client):
        resp = client.get("/api/imgur/album/../../etc")
        assert resp.status_code in (400, 404)

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path_via_api(self, mock_get, client):
        old_id = media_routes.IMGUR_CLIENT_ID
        media_routes.IMGUR_CLIENT_ID = "fake_client_id"
        try:
            mock_get.return_value = _session_get({
                "data": [{"url": "https://i.imgur.com/Abc.jpg", "width": 800, "height": 600, "description": ""}]
            })
            resp = client.get("/api/imgur/album/AbCdEfG")
            assert resp.status_code == 200
            assert len(resp.get_json()["images"]) == 1
        finally:
            media_routes.IMGUR_CLIENT_ID = old_id


# ── /api/og-image ─────────────────────────────────────────────────────────────

class TestOgImage:
    def test_missing_url_returns_400(self, client):
        resp = client.get("/api/og-image?url=")
        assert resp.status_code == 400

    def test_non_http_scheme_returns_400(self, client):
        resp = client.get("/api/og-image?url=javascript:alert(1)")
        assert resp.status_code == 400

    @patch.object(reddit_client.SESSION, "get")
    def test_og_tag_extracted(self, mock_get, client):
        html = b'<meta property="og:image" content="https://example.com/thumb.jpg">'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = iter([html])
        mock_resp.close = MagicMock()
        mock_get.return_value = mock_resp
        # Clear cache first
        embeds_routes._og_cache.clear()
        resp = client.get("/api/og-image?url=https://example.com/article")
        assert resp.status_code == 200
        assert resp.get_json()["url"] == "https://example.com/thumb.jpg"

    @patch.object(reddit_client.SESSION, "get")
    def test_no_og_tag_returns_none(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = iter([b"<html><body>no og tag</body></html>"])
        mock_resp.close = MagicMock()
        mock_get.return_value = mock_resp
        embeds_routes._og_cache.clear()
        resp = client.get("/api/og-image?url=https://example.com/plain")
        assert resp.status_code == 200
        assert resp.get_json()["url"] is None


    @patch.object(embeds_routes, "_resolve_ssrf_safe", side_effect=lambda h: None if h == "internal.test" else "93.184.216.34")
    @patch.object(reddit_client.SESSION, "get")
    def test_redirect_to_private_host_blocked(self, mock_get, _resolve, client):
        mock_get.return_value = MockResponse(status_code=302, headers={"Location": "http://internal.test/"})
        embeds_routes._og_cache.clear()
        resp = client.get("/api/og-image?url=https://example.com/redir")
        assert resp.status_code == 403
        assert mock_get.call_count == 1

    @patch.object(embeds_routes, "_resolve_ssrf_safe", return_value="93.184.216.34")
    @patch.object(reddit_client.SESSION, "get", side_effect=TimeoutError)
    def test_failure_cached_briefly(self, _get, _resolve, client):
        embeds_routes._og_cache.clear()
        with patch.object(embeds_routes._og_cache, "set") as cache_set:
            client.get("/api/og-image?url=https://example.com/slow")
        assert cache_set.call_args.args[2] == embeds_routes.OG_FAIL_CACHE_TTL

    @pytest.mark.parametrize("ip", ["127.0.0.1", "192.168.1.5", "100.100.1.1", "0.0.0.0", "::ffff:10.0.0.1", "fe80::1"])
    def test_non_public_ips_disallowed(self, ip):
        import ipaddress
        assert not embeds_routes._ip_allowed(ipaddress.ip_address(ip))

    def test_public_ip_allowed(self):
        import ipaddress
        assert embeds_routes._ip_allowed(ipaddress.ip_address("93.184.216.34"))

# ── Helper functions ──────────────────────────────────────────────────────────

class TestImgurHelpers:
    def test_imgur_items_gifv_converted(self):
        from routes.media import _imgur_items_to_images
        items = [{"url": "https://i.imgur.com/abc.gifv", "width": 0, "height": 0}]
        result = _imgur_items_to_images(items)
        assert result[0]["url"].endswith(".mp4")

    def test_imgur_items_skips_missing_url(self):
        from routes.media import _imgur_items_to_images
        items = [{"width": 0, "height": 0}]  # no url or link
        result = _imgur_items_to_images(items)
        assert result == []


class TestParseCommentFields:
    def test_basic_fields(self):
        from routes.comments import _parse_comment_fields
        d = {
            "id": "c1", "author": "user", "body": "hello", "score": 42,
            "created_utc": 1700000000, "edited": False, "depth": 2,
            "distinguished": None, "stickied": False,
            "author_flair_text": None, "author_flair_richtext": [],
            "author_flair_type": "text", "author_flair_background_color": None,
            "author_flair_text_color": None, "all_awardings": [],
        }
        result = _parse_comment_fields(d)
        assert result["id"] == "c1"
        assert result["score"] == 42
        assert result["edited_utc"] is None
        assert result["replies"] == []

    def test_edited_utc_numeric(self):
        from routes.comments import _parse_comment_fields
        d = {
            "id": "c2", "body": "x", "score": 0, "created_utc": 0,
            "edited": 1700000000.0, "depth": 0, "distinguished": None,
            "stickied": False, "author_flair_text": None,
            "author_flair_richtext": [], "author_flair_type": "text",
            "author_flair_background_color": None,
            "author_flair_text_color": None, "all_awardings": [],
        }
        result = _parse_comment_fields(d)
        assert result["edited_utc"] == 1700000000.0


class TestParseShredditCrosspost:
    def _post_el(self, inner_html, **attrs):
        from bs4 import BeautifulSoup
        attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        html = f'''<shreddit-post permalink="/r/dest/comments/xpid/some_title/"
            content-href="/r/orig/comments/origid/orig_title/" post-type="crosspost"
            is-crosspost domain="v.redd.it" id="t3_xpid" post-title="some title"
            subreddit-name="dest" author="poster" score="10" comment-count="2"
            created-timestamp="2026-01-01T00:00:00.000000+0000" {attr_str}>
            {inner_html}
        </shreddit-post>'''
        return BeautifulSoup(html, 'html.parser').find('shreddit-post')

    def test_video_crosspost_media_populated(self):
        from shreddit import _parse_shreddit_post
        inner = '''
        <div slot="post-media-container">
          <div class="crosspost-credit-bar"><a href="/r/orig/">r/orig</a></div>
          <div class="crosspost-title"><a href="/r/orig/comments/origid/orig_title/">Orig Title</a></div>
          <shreddit-player src="https://v.redd.it/abc123/HLSPlaylist.m3u8?f=sd" poster="https://external-preview.redd.it/x.png?s=1"></shreddit-player>
        </div>'''
        post = _parse_shreddit_post(self._post_el(inner))
        assert post["crosspost_from"] is not None
        xp = post["crosspost_from"]
        assert xp["subreddit"] == "orig"
        assert xp["id"] == "origid"
        assert xp["title"] == "Orig Title"
        assert xp["is_video"] is True
        assert xp["hls_url"] == "https://v.redd.it/abc123/HLSPlaylist.m3u8"
        assert xp["audio_url"] == "https://v.redd.it/abc123/DASH_audio.mp4"
        assert xp["preview_img"].startswith("/api/img?url=")
        # outer post's own url/linked_post must not be corrupted by the crosspost link
        assert post["url"] == "https://www.reddit.com/r/dest/comments/xpid/some_title/"
        assert post["linked_post"] is None

    def test_image_crosspost_media_populated(self):
        from shreddit import _parse_shreddit_post
        inner = '''
        <div slot="post-media-container">
          <div class="crosspost-credit-bar"><a href="/r/orig/">r/orig</a></div>
          <div class="crosspost-title"><a href="/r/orig/comments/origid/orig_title/">Orig Title</a></div>
          <img data-post-media-primary src="https://preview.redd.it/y.jpeg?s=2">
        </div>'''
        post = _parse_shreddit_post(self._post_el(inner, domain="i.redd.it"))
        xp = post["crosspost_from"]
        assert xp is not None
        assert xp["is_video"] is False
        assert xp["preview_img"].startswith("/api/img?url=")
        assert xp["gallery"] == []

    def test_non_crosspost_untouched(self):
        from shreddit import _parse_shreddit_post
        from bs4 import BeautifulSoup
        html = '''<shreddit-post permalink="/r/dest/comments/id1/t/" post-type="text"
            domain="self.dest" id="t3_id1" post-title="t" subreddit-name="dest"
            author="a" score="1" comment-count="0"
            created-timestamp="2026-01-01T00:00:00.000000+0000"></shreddit-post>'''
        el = BeautifulSoup(html, 'html.parser').find('shreddit-post')
        post = _parse_shreddit_post(el)
        assert post["crosspost_from"] is None


class TestParseShredditPostLinkThumbnail:
    def test_link_post_thumbnail_proxied(self):
        from shreddit import _parse_shreddit_post
        from bs4 import BeautifulSoup
        html = '''<shreddit-post permalink="/r/dest/comments/id1/t/" post-type="link"
            content-href="https://example.com/article" domain="example.com"
            id="t3_id1" post-title="t" subreddit-name="dest"
            author="a" score="1" comment-count="0"
            created-timestamp="2026-01-01T00:00:00.000000+0000">
            <div slot="thumbnail"><a href="https://example.com/article">
                <img src="https://external-preview.redd.it/thumb.png?s=1">
            </a></div>
        </shreddit-post>'''
        el = BeautifulSoup(html, 'html.parser').find('shreddit-post')
        post = _parse_shreddit_post(el)
        assert post["preview_img"].startswith("/api/img?url=")

    def test_link_post_no_thumbnail(self):
        from shreddit import _parse_shreddit_post
        from bs4 import BeautifulSoup
        html = '''<shreddit-post permalink="/r/dest/comments/id1/t/" post-type="link"
            content-href="https://example.com/article" domain="example.com"
            id="t3_id1" post-title="t" subreddit-name="dest"
            author="a" score="1" comment-count="0"
            created-timestamp="2026-01-01T00:00:00.000000+0000"></shreddit-post>'''
        el = BeautifulSoup(html, 'html.parser').find('shreddit-post')
        post = _parse_shreddit_post(el)
        assert post["preview_img"] is None


class TestParseLiveUpdates:
    def test_filters_non_live_update(self):
        from routes.live import _parse_live_updates
        children = [
            {"kind": "LiveUpdate", "data": {
                "id": "u1", "body": "msg", "author": "user",
                "created_utc": 0, "stricken": False,
            }},
            {"kind": "Other", "data": {}},
        ]
        result = _parse_live_updates(children)
        assert len(result) == 1
        assert result[0]["id"] == "u1"


# ── /api/translate ────────────────────────────────────────────────────────────

class TestTranslate:
    def test_missing_text_returns_400(self, client):
        resp = client.get("/api/translate")
        assert resp.status_code == 400

    def test_empty_text_returns_400(self, client):
        resp = client.get("/api/translate?text=")
        assert resp.status_code == 400

    @patch.object(reddit_client.SESSION, "get")
    def test_happy_path(self, mock_get, client):
        mock_get.return_value = _session_get({
            "responseData": {"translatedText": "Hello world"},
            "matches": [{"detected-language": "fr"}],
        })
        resp = client.get("/api/translate?text=Bonjour+monde")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["responseData"]["translatedText"] == "Hello world"

    @patch.object(reddit_client.SESSION, "get")
    def test_upstream_error_returns_502(self, mock_get, client):
        mock_get.side_effect = Exception("network error")
        resp = client.get("/api/translate?text=hello")
        assert resp.status_code == 502
        assert "network error" not in resp.get_data(as_text=True)

    @patch.object(reddit_client.SESSION, "get")
    def test_text_truncated_to_1000_chars(self, mock_get, client):
        mock_get.return_value = _session_get({"responseData": {"translatedText": "x"}})
        long_text = "a" * 2000
        resp = client.get(f"/api/translate?text={long_text}")
        assert resp.status_code == 200
        called_params = mock_get.call_args[1].get("params", {})
        assert len(called_params.get("q", "")) <= 1000


# ── TTLCache ──────────────────────────────────────────────────────────────────

class TestTTLCache:
    def test_expired_entries_swept_before_live_ones_evicted(self):
        cache = helpers.TTLCache(3)
        with patch("helpers.time.time", return_value=1000.0):
            cache.set("live", 1, 5000)
            cache.set("old1", 2, 10)
            cache.set("old2", 3, 10)
        with patch("helpers.time.time", return_value=2000.0):
            cache.set("new", 4, 600)
            assert cache.get("live") == 1
            assert cache.get("new") == 4
            assert len(cache) == 2

    def test_expired_get_is_miss_and_removed(self):
        cache = helpers.TTLCache(10)
        with patch("helpers.time.time", return_value=1000.0):
            cache.set("k", "v", 5)
        with patch("helpers.time.time", return_value=1010.0):
            assert cache.get("k") is helpers._CACHE_MISS
        assert len(cache) == 0

    def test_oldest_evicted_when_nothing_expired(self):
        cache = helpers.TTLCache(2)
        cache.set("a", 1, 600)
        cache.set("b", 2, 600)
        cache.set("c", 3, 600)
        assert cache.get("a") is helpers._CACHE_MISS
        assert cache.get("c") == 3


# ── server_cache ──────────────────────────────────────────────────────────────

class TestServerCache:
    def _app(self):
        from flask import Flask, jsonify
        calls = []
        mini = Flask("server_cache_test")

        @mini.route("/x")
        @helpers.server_cache(60)
        def view():
            calls.append(1)
            return jsonify({"n": len(calls), "s": "é"})

        return mini, calls

    def test_hit_serves_identical_bytes_without_calling_view(self):
        mini, calls = self._app()
        with mini.test_client() as c:
            first = c.get("/x?a=1")
            second = c.get("/x?a=1")
        assert len(calls) == 1
        assert second.data == first.data
        assert second.is_json and second.get_json() == {"n": 1, "s": "é"}
        assert second.headers["Cache-Control"] == "public, max-age=60"

    def test_distinct_query_is_separate_entry(self):
        mini, calls = self._app()
        with mini.test_client() as c:
            c.get("/x?a=1")
            c.get("/x?a=2")
        assert len(calls) == 2
