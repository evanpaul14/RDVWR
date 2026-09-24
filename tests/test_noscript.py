"""Tests for the server-rendered pages and their no-JS (noscript) fallback:
routes/pages.py, routes/page_data.py, routes/ns_settings.py, ns_prefs.py,
template_helpers.py, and the noscript HTML sanitizer in media_detection.py.

Reddit is mocked per URL (see _reddit), so no network traffic is made."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import re
import pytest
from unittest.mock import patch

import helpers
import reddit_client
from app import app
from reddit_html import sanitize_reddit_html
from tests.test_routes import MockResponse, _make_listing, _make_post


@pytest.fixture(autouse=True)
def no_oauth(monkeypatch):
    monkeypatch.setattr(reddit_client, "REDDIT_OAUTH", False)
    helpers._view_cache.clear()


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.environ_base = {"HTTP_SEC_FETCH_SITE": "same-origin"}
        yield c


def _reddit(routes):
    """A SESSION.get side_effect answering each request from the first `routes` entry
    whose regex matches the URL: {pattern: data | MockResponse}. Anything else 404s."""
    def get(url, *args, **kwargs):
        for pattern, answer in routes.items():
            if re.search(pattern, url):
                return answer if isinstance(answer, MockResponse) else MockResponse(answer)
        return MockResponse({}, status_code=404)
    return get


def _noscript(resp):
    """The <noscript> fallback markup of a rendered page."""
    html = resp.get_data(as_text=True)
    m = re.search(r'<noscript>(?!<style)(.*)</noscript>', html, re.S)
    assert m, "page has no noscript fallback"
    return m.group(1)


def _comments_payload(comments=()):
    post = {**_make_post("abc123", "Post title", "testsub"), "author": "op",
            "permalink": "/r/testsub/comments/abc123/post_title/"}
    return [{"data": {"children": [{"kind": "t3", "data": post}]}},
            {"data": {"children": list(comments)}}]


def _comment(cid, body="hi", author="someone", replies=None):
    return {"kind": "t1", "data": {
        "id": cid, "author": author, "body": body, "body_html": f"&lt;p&gt;{body}&lt;/p&gt;",
        "score": 1, "created_utc": 1700000000,
        "replies": {"data": {"children": replies}} if replies else "",
    }}


# ── Preferences ───────────────────────────────────────────────────────────────

class TestPrefs:
    def test_defaults_come_from_default_settings(self):
        from ns_prefs import all_prefs
        with app.test_request_context("/"):
            prefs = all_prefs()
        assert prefs["theme"] == helpers.DEFAULT_SETTINGS["theme"]

    def test_invalid_enum_cookie_falls_back_to_default(self):
        from ns_prefs import get_pref
        with app.test_request_context("/", headers={"Cookie": "ns_layout=bogus"}):
            assert get_pref("layout") == helpers.DEFAULT_SETTINGS["layout"]

    def test_off_cookie_overrides_an_on_default(self, monkeypatch):
        from ns_prefs import get_pref
        monkeypatch.setitem(helpers.DEFAULT_SETTINGS, "nsfwBlur", True)
        with app.test_request_context("/", headers={"Cookie": "ns_nsfw_blur=0"}):
            assert get_pref("nsfw_blur") is False


class TestSettingsPage:
    def test_get_renders_form(self, client):
        html = _noscript(client.get("/settings?next=/r/pics"))
        assert 'name="next" value="/r/pics"' in html
        assert 'name="theme"' in html

    def test_post_sets_cookies_and_redirects(self, client):
        resp = client.post("/settings", data={"next": "/r/pics", "theme": "light", "layout": "bogus",
                                              "nsfw_blur": "1"})
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/r/pics"
        cookies = resp.headers.getlist("Set-Cookie")
        assert any(c.startswith("ns_theme=light") for c in cookies)
        assert any(c.startswith("ns_nsfw_blur=1") for c in cookies)
        # An invalid enum value clears the cookie instead of storing it.
        assert any(c.startswith("ns_layout=;") for c in cookies)

    @pytest.mark.parametrize("bad", ["//evil.example", "https://evil.example", "/\\evil.example"])
    def test_post_rejects_offsite_next(self, client, bad):
        resp = client.post("/settings", data={"next": bad})
        assert resp.headers["Location"] == "/"

    def test_prefs_apply_to_pages(self, client):
        client.set_cookie("ns_theme", "light")
        client.set_cookie("ns_layout", "minimal")
        resp = client.get("/saved")
        html = resp.get_data(as_text=True)
        assert 'class="theme-light minimal-mode' in html


# ── Sanitizer ─────────────────────────────────────────────────────────────────

class TestSanitizer:
    def test_spoiler_kept_as_spoiler(self):
        out = sanitize_reddit_html('<p><span class="md-spoiler-text">secret</span></p>')
        assert '<span class="spoiler" tabindex="0">secret</span>' in out

    def test_other_spans_dropped(self):
        assert sanitize_reddit_html('<p><span style="x">a</span></p>') == '<p>a</p>'

    def test_reddit_links_become_local(self):
        out = sanitize_reddit_html('<a href="https://old.reddit.com/r/pics/comments/abc/x/">l</a>')
        assert out == '<a href="/r/pics/comments/abc/x/">l</a>'

    def test_subreddit_search_links_become_local_search(self):
        out = sanitize_reddit_html('<a href="/r/pics/search?q=flair%3AOC&amp;restrict_sr=on">OC</a>')
        assert out == '<a href="/search?q=flair%3AOC&amp;sub=pics">OC</a>'

    def test_other_relative_links_go_to_reddit(self):
        out = sanitize_reddit_html('<a href="/message/compose?to=x">m</a>')
        assert 'href="https://www.reddit.com/message/compose?to=x" target="_blank"' in out

    def test_outside_links_open_in_new_tab(self):
        out = sanitize_reddit_html('<a href="https://example.com/">l</a>')
        assert 'target="_blank"' in out

    def test_unsafe_links_lose_href(self):
        assert sanitize_reddit_html('<a href="javascript:alert(1)">x</a>') == '<a>x</a>'

    def test_bare_reddit_image_link_inlined(self):
        url = "https://preview.redd.it/a.png?width=1&amp;s=2"
        out = sanitize_reddit_html(f'<a href="{url}">{url}</a>')
        assert '<img src="/api/img?url=https%3A%2F%2Fpreview.redd.it%2Fa.png' in out

    def test_captioned_image_link_keeps_caption(self):
        out = sanitize_reddit_html('<a href="https://preview.redd.it/a.png">my cat</a>')
        assert 'md-img-caption">my cat<' in out

    def test_giphy_inlined_and_marked_third_party(self):
        out = sanitize_reddit_html('<a href="https://giphy.com/gifs/abc123">https://giphy.com/gifs/abc123</a>')
        assert 'class="md-ext-media"' in out
        assert 'src="https://media.giphy.com/media/abc123/giphy.gif"' in out

    def test_script_and_style_text_dropped(self):
        assert sanitize_reddit_html('<p>a<script>x()</script><style>p{}</style>b</p>') == '<p>ab</p>'


class TestTemplateHelpers:
    def test_md_unembeds_third_party_media_when_asked(self):
        from template_helpers import md
        html = sanitize_reddit_html('<a href="https://giphy.com/gifs/abc123">https://giphy.com/gifs/abc123</a>')
        with app.test_request_context("/", headers={"Cookie": "ns_link_external_media=1"}):
            assert '<img' not in md(html)
        with app.test_request_context("/"):
            assert '<img' in md(html)

    def test_download_url_for_reddit_video(self):
        from template_helpers import download_url
        p = {"id": "x", "is_video": True, "video_url": "https://v.redd.it/a/DASH_720.mp4",
             "hls_url": "https://v.redd.it/a/HLSPlaylist.m3u8"}
        assert download_url(p).startswith("/api/download/reddit-video?")

    def test_download_url_skips_unknown_hosts(self):
        from template_helpers import download_url
        assert download_url({"id": "x", "preview_img": "https://example.com/a.jpg"}) is None

    def test_is_bot(self):
        from template_helpers import is_bot
        assert is_bot("RemindMeBot") and is_bot("bot_2000")
        assert not is_bot("Robotics")


# ── Pages ─────────────────────────────────────────────────────────────────────

class TestSubredditPage:
    @patch.object(reddit_client.SESSION, "get")
    def test_renders_feed_and_hydration(self, mock_get, client):
        mock_get.side_effect = _reddit({
            r"/r/testsub/hot\.json": _make_listing([_make_post("p1", "First post", "testsub")], after="t3_next"),
            r"/r/testsub/about\.json": {"data": {"title": "Test Sub", "subscribers": 1200,
                                                  "description_html": "&lt;p&gt;Welcome&lt;/p&gt;"}},
            r"/about/rules\.json": {"rules": [{"short_name": "Be nice"}]},
            r"/api/widgets\.json": {"items": {}, "layout": {}},
        })
        resp = client.get("/r/testsub/hot")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "window.__INITIAL_DATA__" in html and '"_sub": "testsub"' in html
        ns = _noscript(resp)
        assert "First post" in ns
        assert "Be nice" in ns and "Welcome" in ns
        assert 'href="/r/testsub/hot?after=t3_next"' in ns
        assert "<title>r/testsub — RDVWR</title>" in html

    @patch.object(reddit_client.SESSION, "get")
    def test_unknown_subreddit_redirects_to_community_search(self, mock_get, client):
        mock_get.side_effect = _reddit({})
        resp = client.get("/r/nosuchsub")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/search?q=nosuchsub&stype=communities"

    @patch.object(reddit_client.SESSION, "get")
    def test_quarantined_offers_continue(self, mock_get, client):
        mock_get.side_effect = _reddit({
            r"/hot\.json": MockResponse({"reason": "quarantined", "quarantine_message": "Careful"}, 403),
        })
        ns = _noscript(client.get("/r/qsub/hot"))
        assert "This subreddit is quarantined" in ns
        assert 'href="/r/qsub/hot?quarantine_opt_in=1"' in ns

    @patch.object(reddit_client.SESSION, "get")
    def test_nsfw_hide_pref_filters_posts(self, mock_get, client):
        posts = [{**_make_post("p1", "Safe", "testsub")}, {**_make_post("p2", "Spicy", "testsub"), "over_18": True}]
        mock_get.side_effect = _reddit({r"/hot\.json": _make_listing(posts)})
        client.set_cookie("ns_nsfw_hide", "1")
        ns = _noscript(client.get("/r/testsub/hot"))
        assert "Safe" in ns and "Spicy" not in ns


    @patch.object(reddit_client.SESSION, "get")
    def test_spoiler_and_nsfw_media_veiled(self, mock_get, client):
        img = {"preview": {"images": [{"source": {"url": "https://i.redd.it/a.jpg", "width": 10, "height": 10}}]},
               "post_hint": "image", "url": "https://i.redd.it/a.jpg", "domain": "i.redd.it"}
        posts = [{**_make_post("p1", "Spoiled", "testsub"), **img, "spoiler": True},
                 {**_make_post("p2", "Spicy", "testsub"), **img, "over_18": True}]
        mock_get.side_effect = _reddit({r"/hot\.json": _make_listing(posts)})
        ns = _noscript(client.get("/r/testsub/hot"))
        assert "ns-reveal--spoiler" in ns and "ns-reveal--nsfw" not in ns
        client.set_cookie("ns_nsfw_blur", "1")
        assert "ns-reveal--nsfw" in _noscript(client.get("/r/testsub/hot"))


    @patch.object(reddit_client.SESSION, "get")
    def test_feed_gallery_shows_only_first_image(self, mock_get, client):
        gallery = {"is_gallery": True, "url": "https://www.reddit.com/gallery/p1",
                   "gallery_data": {"items": [{"media_id": "m1"}, {"media_id": "m2"}, {"media_id": "m3"}]},
                   "media_metadata": {m: {"status": "valid", "e": "Image", "m": "image/jpg",
                                          "s": {"u": f"https://preview.redd.it/{m}.jpg", "x": 10, "y": 10}}
                                      for m in ("m1", "m2", "m3")}}
        mock_get.side_effect = _reddit({r"/hot\.json": _make_listing([{**_make_post("p1", "Album", "testsub"), **gallery}])})
        ns = _noscript(client.get("/r/testsub/hot"))
        assert "ns-gallery-preview" in ns and "ns-gallery-item" not in ns
        assert 'href="/r/testsub/comments/p1" title="View all 3 images"' in ns


    @patch.object(reddit_client.SESSION, "get")
    def test_video_offers_hls_then_mp4(self, mock_get, client):
        video = {"is_video": True, "media": {"reddit_video": {
            "fallback_url": "https://v.redd.it/vid1/DASH_720.mp4",
            "hls_url": "https://v.redd.it/vid1/HLSPlaylist.m3u8"}}}
        mock_get.side_effect = _reddit({r"/hot\.json": _make_listing([{**_make_post("p1", "Clip", "testsub"), **video}])})
        ns = _noscript(client.get("/r/testsub/hot"))
        assert ns.index("HLSPlaylist.m3u8") < ns.index("DASH_720.mp4")


class TestPager:
    def _urls(self, path, next_url):
        from template_helpers import pager_urls
        with app.test_request_context(path):
            return pager_urls(next_url)

    def test_first_page_has_no_previous(self):
        assert self._urls("/r/pics/hot", "/r/pics/hot?after=t3_a") == (None, "/r/pics/hot?after=t3_a")

    def test_trail_pushes_and_pops(self):
        prev, nxt = self._urls("/r/pics/hot?after=t3_a", "/r/pics/hot?after=t3_b")
        assert prev == "/r/pics/hot" and nxt == "/r/pics/hot?after=t3_b&prev=t3_a"
        prev, nxt = self._urls("/r/pics/hot?after=t3_b&prev=t3_a", "/r/pics/hot?after=t3_c")
        assert prev == "/r/pics/hot?after=t3_a"
        assert nxt == "/r/pics/hot?after=t3_c&prev=t3_a%2Ct3_b"

    def test_keeps_other_params_and_drops_bad_cursors(self):
        prev, _ = self._urls("/search?q=cat&after=t3_b&prev=t3_a,<x>", None)
        assert prev == "/search?q=cat&after=t3_a"

    @patch.object(reddit_client.SESSION, "get")
    def test_feed_renders_prev_and_next_buttons(self, mock_get, client):
        mock_get.side_effect = _reddit({r"/hot\.json": _make_listing([_make_post("p2", "Two", "testsub")], after="t3_next")})
        ns = _noscript(client.get("/r/testsub/hot?after=t3_p1"))
        assert 'class="ns-page-btn" href="/r/testsub/hot" rel="prev"' in ns
        assert 'href="/r/testsub/hot?after=t3_next&amp;prev=t3_p1" rel="next"' in ns


class TestPostPage:
    @patch.object(reddit_client.SESSION, "get")
    def test_renders_comments_and_more_link(self, mock_get, client):
        more = {"kind": "more", "data": {"id": "m1", "children": ["c8", "c9"], "count": 2}}
        mock_get.side_effect = _reddit({r"/comments/abc123\.json": _comments_payload([_comment("c1", "Top comment"), more])})
        resp = client.get("/r/testsub/comments/abc123/post_title/")
        ns = _noscript(resp)
        assert "Top comment" in ns
        assert 'href="/r/testsub/comments/abc123/_/c1"' in ns
        assert "more=c8%2Cc9" in ns and "Load 2 more comments" in ns
        assert "window.__INITIAL_POST__" in resp.get_data(as_text=True)

    @patch.object(reddit_client.SESSION, "get")
    def test_meta_matches_js_post_view(self, mock_get, client):
        payload = _comments_payload()
        post = payload[0]["data"]["children"][0]["data"]
        post.update(edited=1700000000, all_awardings=[
            {"name": "Gold", "count": 2, "icon_url": "https://i.redd.it/x.png", "resized_icons": []}])
        mock_get.side_effect = _reddit({r"/comments/abc123\.json": payload})
        ns = _noscript(client.get("/r/testsub/comments/abc123"))
        meta = re.search(r'<div class="pv-meta">.*?</div>', ns, re.S).group(0)
        # plain time span (inherits .pv-meta sizing, like postview.js), edited age shown
        assert 'class="meta-item" title' not in meta and "*edited " in meta
        client.set_cookie("ns_layout", "minimal")
        ns = _noscript(client.get("/r/testsub/comments/abc123"))
        assert '<span class="awards" title="Gold">&#127941;2</span>' in ns

    @patch.object(reddit_client.SESSION, "get")
    def test_more_page_shows_loaded_comments(self, mock_get, client):
        mock_get.side_effect = _reddit({
            r"/comments/abc123\.json": _comments_payload([_comment("c1", "Top comment")]),
            r"/api/morechildren\.json": {"json": {"data": {"things": [
                {"kind": "t1", "data": {"id": "c8", "author": "x", "body": "Loaded later", "parent_id": "t3_abc123"}}]}}},
        })
        ns = _noscript(client.get("/r/testsub/comments/abc123?more=c8,c9&sort=top"))
        assert "Loaded later" in ns and "Top comment" not in ns
        assert "View full thread" in ns

    @patch.object(reddit_client.SESSION, "get")
    def test_comment_permalink_shows_thread_with_context_link(self, mock_get, client):
        tree = [_comment("c1", "Parent", replies=[_comment("c2", "Target reply")])]
        mock_get.side_effect = _reddit({r"/comments/abc123\.json": _comments_payload(tree)})
        ns = _noscript(client.get("/r/testsub/comments/abc123/_/c2"))
        assert "Target reply" in ns and "Parent" not in ns
        assert "/r/testsub/comments/abc123/_/c2?sort=confidence&amp;context=1" in ns

    @patch.object(reddit_client.SESSION, "get")
    def test_missing_post(self, mock_get, client):
        mock_get.side_effect = _reddit({})
        assert "Post not found" in _noscript(client.get("/r/testsub/comments/zzz999"))

    def test_invalid_ids_404(self, client):
        assert client.get("/r/testsub/comments/not-an-id!").status_code == 404


class TestProfilePage:
    @patch.object(reddit_client.SESSION, "get")
    def test_overview_hydrates(self, mock_get, client):
        mock_get.side_effect = _reddit({
            r"/user/someone/overview\.json": _make_listing([_make_post("p1", "My post", "testsub")]),
            r"/user/someone/about\.json": {"data": {"name": "someone", "link_karma": 5}},
        })
        resp = client.get("/user/someone")
        assert "window.__INITIAL_PROFILE__" in resp.get_data(as_text=True)
        assert "My post" in _noscript(resp)

    @patch.object(reddit_client.SESSION, "get")
    def test_comments_tab_not_hydrated(self, mock_get, client):
        mock_get.side_effect = _reddit({
            r"/user/someone/comments\.json": {"data": {"children": [{"kind": "t1", "data": {
                "id": "c1", "body": "A comment", "subreddit": "testsub", "link_id": "t3_abc123",
                "link_title": "Thread"}}], "after": None}},
        })
        resp = client.get("/user/someone?tab=comments&sort=top&t=week")
        assert "__INITIAL_PROFILE__" not in resp.get_data(as_text=True)
        ns = _noscript(resp)
        assert "A comment" in ns
        assert 'href="/r/testsub/comments/abc123/_/c1"' in ns
        assert mock_get.call_args_list[0][1]["params"]["t"] == "week"

    @patch.object(reddit_client.SESSION, "get")
    def test_missing_user_offers_archive(self, mock_get, client):
        mock_get.side_effect = _reddit({})
        ns = _noscript(client.get("/user/ghost"))
        assert "User not found or profile is private" in ns
        assert 'href="/user/ghost?archive=1"' in ns


class TestSearchPage:
    @pytest.mark.parametrize("q,target", [
        ("r/pics", "/r/pics"), ("u/spez", "/user/spez"), ("u/spez/m/news", "/user/spez/m/news")])
    def test_header_search_shortcuts(self, client, q, target):
        resp = client.get(f"/search?q={q}")
        assert resp.status_code == 302 and resp.headers["Location"] == target

    @patch.object(reddit_client.SESSION, "get")
    def test_scoped_search(self, mock_get, client):
        mock_get.side_effect = _reddit({r"/r/pics/search\.json": _make_listing([_make_post("p1", "Cat pic", "pics")])})
        ns = _noscript(client.get("/search?q=cats&sub=pics&sort=top&t=week"))
        assert "Cat pic" in ns
        assert "search-type-bar" not in ns          # scoped searches are posts-only
        assert 'href="/search?q=cats&amp;sort=top&amp;t=week&amp;scope=pics"' in ns   # untick scope
        assert mock_get.call_args[1]["params"]["restrict_sr"] == 1

    def test_empty_query(self, client):
        assert "Enter a search term" in _noscript(client.get("/search"))


class TestOtherPages:
    @patch.object(reddit_client.SESSION, "get")
    def test_live_thread(self, mock_get, client):
        mock_get.side_effect = _reddit({
            r"/live/abc/about\.json": {"data": {"title": "Big event", "state": "live"}},
            r"/live/abc\.json": {"data": {"children": [{"kind": "LiveUpdate", "data": {
                "id": "u1", "author": "reporter", "body_html": "&lt;p&gt;Update one&lt;/p&gt;"}}], "after": "LiveUpdate_x"}},
        })
        ns = _noscript(client.get("/live/abc"))
        assert "Big event" in ns and "Update one" in ns
        assert 'href="/live/abc?after=LiveUpdate_x"' in ns

    @patch.object(reddit_client.SESSION, "head")
    def test_share_link_redirects_to_local_post(self, mock_head, client):
        mock_head.return_value = MockResponse(status_code=301, headers={
            "Location": "https://www.reddit.com/r/pics/comments/abc123/title/?share_id=x"})
        resp = client.get("/r/pics/s/AbC123")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/r/pics/comments/abc123/title/"

    def test_js_only_pages(self, client):
        assert "needs JavaScript" in _noscript(client.get("/saved"))

    def test_unknown_path_is_html_404(self, client):
        resp = client.get("/no/such/page")
        assert resp.status_code == 404
        assert "Page not found" in _noscript(resp)

    def test_unknown_api_path_is_json_404(self, client):
        resp = client.get("/api/no-such-endpoint")
        assert resp.status_code == 404 and resp.is_json


class TestJsonPassthrough:
    @patch.object(reddit_client.SESSION, "get")
    def test_allowed_path_is_proxied(self, mock_get, client):
        upstream = MockResponse({"kind": "Listing"})
        upstream.content = b'{"kind": "Listing"}'
        mock_get.return_value = upstream
        resp = client.get("/user/spez/about.json")
        assert resp.status_code == 200 and resp.get_json() == {"kind": "Listing"}

    def test_disallowed_path_404s(self, client):
        assert client.get("/api/v1/me.json").status_code in (403, 404)
        assert client.get("/r/pics/api/morechildren.json").status_code == 404

    def test_static_json_untouched(self, client):
        assert client.get("/static/manifest.json").status_code == 200
