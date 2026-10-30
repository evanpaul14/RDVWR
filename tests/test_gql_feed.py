"""GraphQL feed fallback: gql_posts converter + fetch_feed's 403 backup.

All network access is mocked."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import base64
import pytest
from unittest.mock import patch, MagicMock

import gql_posts
from gql_posts import gql_post_to_rest, fetch_gql_feed
from helpers import UpstreamError
from media_detection import process_post
from routes import subreddit as subreddit_routes


def gql_post(pid="t3_abc", **over):
    n = {"__typename": "SubredditPost", "id": pid, "postTitle": "A title", "score": 12, "upvoteRatio": 0.97,
         "commentCount": 3, "createdAt": "2026-10-29T10:00:00.000000+0000", "editedAt": None,
         "authorInfo": {"name": "someone"}, "subreddit": {"name": "pics"}, "isSelfPost": False,
         "isNsfw": False, "isStickied": False, "isSpoiler": False, "isLocked": False,
         "permalink": "/r/pics/comments/abc/a_title/", "domain": "i.redd.it", "voteState": "NONE",
         "url": "https://i.redd.it/x.jpeg", "media": None, "awardings": []}
    n.update(over)
    return n


def source(w, h, name="x"):
    return {"dimensions": {"width": w, "height": h}, "url": f"https://preview.redd.it/{name}.jpeg?width={w}"}


def title_cell(pid):
    return {"__typename": "TitleCell", "id": base64.b64encode(f"TitleCell-{pid}".encode()).decode()}


def feed_page(ids, cursor=None):
    edges = [{"node": {"adPayload": None, "cells": [{"__typename": "MetadataCell", "id": "m"}, title_cell(i)]}}
             for i in ids]
    return {"data": {"subredditV3": {"elements": {"edges": edges, "pageInfo": {"endCursor": cursor}}}}}


def test_basic_fields_and_image_preview():
    still = {"source": source(1080, 900), "small": source(108, 90), "xlarge": source(640, 533),
             "large": None}
    out = process_post(gql_post_to_rest(gql_post(media={"typeHint": "IMAGE", "still": still})))
    assert (out["id"], out["author"], out["subreddit"], out["score"]) == ("abc", "someone", "pics", 12)
    assert out["upvote_ratio"] == 97
    assert out["permalink"] == "https://www.reddit.com/r/pics/comments/abc/a_title/"
    assert out["created_utc"] > 0
    assert "width%3D640" in out["thumb_url"]   # the >=640px rendition becomes the card thumbnail


def test_reddit_video_gets_hls_and_muxed_fallback():
    media = {"typeHint": "VIDEO", "streaming": {"hlsUrl": "https://v.redd.it/abc/HLSPlaylist.m3u8?f=sd", "isGif": False},
             "packagedMedia": {"muxedMp4s": {"recommended": {"url": "https://packaged-media.redd.it/abc/m.mp4"}}}}
    out = process_post(gql_post_to_rest(gql_post(url="https://v.redd.it/abc", domain="v.redd.it", media=media)))
    assert out["is_video"] and "HLSPlaylist.m3u8" in out["hls_url"]
    assert out["video_url"] == "https://packaged-media.redd.it/abc/m.mp4"


def test_gif_is_served_from_its_url_not_as_video():
    media = {"typeHint": "GIFVIDEO", "animated": {"mp4_source": {"url": "https://preview.redd.it/g.gif?format=mp4"}}}
    out = process_post(gql_post_to_rest(gql_post(url="https://i.redd.it/g.gif", media=media)))
    assert out["gif_url"] == "https://i.redd.it/g.gif" and not out["is_video"]


def test_reddit_copy_of_external_clip_is_a_preview_not_a_video_post():
    media = {"typeHint": "EMBED", "streaming": {"hlsUrl": "https://v.redd.it/abc/HLSPlaylist.m3u8?f=sd", "isGif": True},
             "packagedMedia": {"muxedMp4s": {"recommended": {"url": "https://packaged-media.redd.it/abc/m.mp4"}}},
             "video": {"embedHtml": "<iframe></iframe>"}}
    out = process_post(gql_post_to_rest(gql_post(url="https://www.redgifs.com/watch/somename", domain="redgifs.com",
                                                 media=media)))
    assert out["redgifs_id"] == "somename" and not out["is_video"] and out["video_url"] is None
    assert "HLSPlaylist.m3u8" in out["redgifs_fallback_hls"]
    assert out["redgifs_fallback_url"] == "https://packaged-media.redd.it/abc/m.mp4"


def test_embed_without_html_still_gets_the_embed_url():
    out = process_post(gql_post_to_rest(gql_post("t3_tw1", url="https://clips.twitch.tv/SomeClip", domain="clips.twitch.tv",
                                                 media={"typeHint": "EMBED"})))
    assert out["embed_url"] == "https://www.redditmedia.com/mediaembed/tw1"


def test_poll():
    poll = {"options": [{"id": "1", "text": "a", "voteCount": 4}, {"id": "2", "text": "b", "voteCount": 6}],
            "votingEndsAt": "2020-01-01T00:00:00.000000+0000"}
    out = process_post(gql_post_to_rest(gql_post(isSelfPost=True, poll=poll)))
    assert out["poll"]["total_votes"] == 10 and out["poll"]["closed"]
    assert [o["vote_count"] for o in out["poll"]["options"]] == [4, 6]


def test_crosspost_carries_parent_media():
    parent = gql_post("t3_par", subreddit={"name": "src"}, url="https://v.redd.it/par", domain="v.redd.it", media={
        "typeHint": "VIDEO", "streaming": {"hlsUrl": "https://v.redd.it/par/HLSPlaylist.m3u8"}})
    out = process_post(gql_post_to_rest(gql_post(crosspostRoot={"post": parent})))
    assert out["crosspost_from"]["subreddit"] == "src" and out["crosspost_from"]["hls_url"]


def test_gallery():
    item = {"id": "9", "caption": "cap", "media": {"id": "m1", "url": "https://i.redd.it/m1.jpeg", "width": 800,
                                                   "height": 600, "small": source(108, 81, "m1"),
                                                   "xlarge": source(640, 480, "m1")}}
    out = process_post(gql_post_to_rest(gql_post(url="https://www.reddit.com/gallery/abc", gallery={"items": [item]})))
    assert out["gallery"][0]["url"] == "https://i.redd.it/m1.jpeg"
    assert out["gallery"][0]["caption"] == "cap" and out["gallery"][0]["width"] == 800
    assert "width%3D640" in out["gallery"][0]["thumb"]


def test_selftext_falls_back_to_paragraphs_and_escapes():
    post = gql_post(isSelfPost=True, content={"markdown": "hi <b>x</b>\nsee https://example.com/a?b=1\n\nsecond"})
    html = process_post(gql_post_to_rest(post))["selftext_html"]   # already sanitized by process_post
    assert "<b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert 'href="https://example.com/a?b=1"' in html
    assert html.count("<p>") == 2


def rt(*blocks):
    import json
    return json.dumps({"document": list(blocks)})


def test_selftext_renders_richtext_formatting_lists_and_code():
    par = {"e": "par", "c": [{"e": "text", "t": "bold and code", "f": [[1, 0, 4], [64, 9, 4]]},
                             {"e": "link", "t": "a link", "u": "https://example.com/"}]}
    lst = {"e": "list", "o": True, "c": [{"e": "li", "c": [{"e": "par", "c": [{"e": "text", "t": "one"}]}]}]}
    code = {"e": "code", "c": [{"e": "raw", "t": "a < b"}, {"e": "raw", "t": "c"}]}
    quote = {"e": "blockquote", "c": [{"e": "par", "c": [{"e": "text", "t": "quoted", "f": [[2, 0, 6]]}]}]}
    spoiler = {"e": "par", "c": [{"e": "spoilertext", "c": [{"e": "text", "t": "secret"}]}]}
    post = gql_post(isSelfPost=True, content={"markdown": "x", "richtext": rt(par, lst, code, quote, spoiler)})
    html = process_post(gql_post_to_rest(post))["selftext_html"]
    assert "<strong>bold</strong> and <code>code</code>" in html
    assert 'href="https://example.com/"' in html and "<ol>" in html and "<li><p>one</p></li>" in html
    assert "<pre><code>a &lt; b\nc</code></pre>" in html
    assert "<blockquote><p><em>quoted</em></p></blockquote>" in html
    assert 'class="spoiler"' in html and "secret" in html


def test_richtext_table_and_heading():
    from gql_richtext import richtext_html
    cell = lambda t: {"c": [{"e": "text", "t": t}]}
    doc = rt({"e": "h", "l": 2, "c": [{"e": "raw", "t": "Title"}]},
             {"e": "table", "h": [cell("A"), cell("B")], "c": [[cell("1"), cell("2")]]})
    assert richtext_html(doc) == ("<h2>Title</h2><table><thead><tr><th>A</th><th>B</th></tr></thead>"
                                  "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>")


def test_richtext_format_offsets_count_utf16_units():
    from gql_richtext import richtext_html
    doc = rt({"e": "par", "c": [{"e": "text", "t": "\U0001F600 bold", "f": [[1, 3, 4]]}]})   # the emoji is 2 units
    assert richtext_html(doc) == "<p>\U0001F600 <strong>bold</strong></p>"


def test_richtext_garbage_gives_empty_html():
    from gql_richtext import richtext_html
    assert richtext_html("not json") == "" and richtext_html(None) == "" and richtext_html("{}") == ""


def test_selftext_image_refs_become_urls():
    content = {"markdown": "look\n\n![img](abc123)", "richtextMedia": [{"id": "abc123", "url": "https://i.redd.it/abc123.png"}]}
    out = process_post(gql_post_to_rest(gql_post(isSelfPost=True, content=content)))
    assert "![img](https://i.redd.it/abc123.png)" in out["selftext"]


def test_profile_post_subreddit_from_permalink():
    n = gql_post(subreddit=None, permalink="/user/someone/comments/abc/title/")
    assert gql_post_to_rest(n)["subreddit"] == "u_someone"


def test_youtube_embed():
    media = {"typeHint": "EMBED", "video": {"embedHtml": "<iframe></iframe>", "dimensions": {"width": 356, "height": 200},
                                            "attribution": {"providerName": "YouTube"}}}
    out = process_post(gql_post_to_rest(gql_post(url="https://youtu.be/EREm4M-34-4", domain="youtu.be", media=media)))
    assert out["youtube_id"] == "EREm4M-34-4"


def test_fetch_gql_feed_pages_until_enough_posts_and_skips_ads():
    first = feed_page([f"t3_{i:03d}" for i in range(14)], "c1")
    first["data"]["subredditV3"]["elements"]["edges"].append(
        {"node": {"adPayload": {"x": 1}, "cells": [title_cell("t3_ad")]}})
    second = feed_page([f"t3_{i:03d}" for i in range(14, 28)], "c2")
    ids = [f"t3_{i:03d}" for i in range(28)]
    calls = []

    def fake(op, variables, timeout=15):
        calls.append((op, variables))
        if op == "SubredditFeedSdui":
            return first if "after" not in variables else second
        assert variables["ids"] == ids
        return {"data": {"postsInfoByIds": [gql_post(i) for i in reversed(ids)]}}

    with patch.object(gql_posts, "gql_query", fake):
        feed = fetch_gql_feed("pics", "top", "week")
    assert calls[0][1] == {"subredditName": "pics", "sort": "TOP", "time": "WEEK"}
    assert [p["id"] for p in feed["posts"]] == [i[3:] for i in ids]   # feed order, not response order
    assert feed["after"] == "c2"


def test_fetch_gql_feed_missing_subreddit():
    with patch.object(gql_posts, "gql_query", lambda *a, **k: {"data": {"subredditV3": {"elements": None}}}):
        with pytest.raises(UpstreamError) as e:
            fetch_gql_feed("nope", "hot")
    assert e.value.status == 404 and e.value.state == "not_found"


def waf_response():
    r = MagicMock(status_code=403, headers={"content-type": "text/html"})
    r.json.side_effect = ValueError
    return r


def test_fetch_feed_falls_back_to_graphql_on_waf_block():
    canned = {"posts": [{"id": "abc"}], "after": "c1"}
    with patch.object(subreddit_routes, "reddit_get", return_value=waf_response()), \
         patch.object(subreddit_routes, "fetch_gql_feed", return_value=canned) as gql, \
         patch.object(subreddit_routes, "hydrate_linked_posts"):
        assert subreddit_routes.fetch_feed("waf_sub1", "hot") == canned
    gql.assert_called_once()


def test_fetch_feed_private_subreddit_does_not_use_graphql():
    private = MagicMock(status_code=403, headers={"content-type": "application/json"})
    private.json.return_value = {"reason": "private"}
    with patch.object(subreddit_routes, "reddit_get", return_value=private), \
         patch.object(subreddit_routes, "fetch_gql_feed") as gql:
        with pytest.raises(UpstreamError) as e:
            subreddit_routes.fetch_feed("private_sub1", "hot")
    assert e.value.state == "private"
    gql.assert_not_called()


def test_fetch_feed_graphql_failure_reports_the_original_block():
    with patch.object(subreddit_routes, "reddit_get", return_value=waf_response()), \
         patch.object(subreddit_routes, "fetch_gql_feed", side_effect=RuntimeError("boom")):
        with pytest.raises(UpstreamError) as e:
            subreddit_routes.fetch_feed("waf_sub2", "hot")
    assert e.value.status == 403


# ── about / rules ─────────────────────────────────────────────────────────────

import gql_subreddit
from gql_subreddit import fetch_gql_about, fetch_gql_rules


def about_node(**over):
    n = {"__typename": "Subreddit", "title": "Pics", "publicDescriptionText": "Photos", "subscribersCount": 1234.0,
         "isSubscribed": False, "type": "PUBLIC", "isQuarantined": False,
         "styles": {"legacyIcon": {"url": "https://b.thumbs.redditmedia.com/i.png"}},
         "description": {"markdown": "**side**", "richtext": rt({"e": "par", "c": [{"e": "text", "t": "side", "f": [[1, 0, 4]]}]})}}
    n.update(over)
    return n


def about_response(node):
    return lambda *a, **k: {"data": {"subredditInfoByName": node}}


def test_gql_about_maps_to_rest_shape():
    with patch.object(gql_subreddit, "gql_query", about_response(about_node())):
        a = fetch_gql_about("pics")
    assert (a["title"], a["description"], a["subscribers"], a["state"], a["active"]) == ("Pics", "Photos", 1234, None, 0)
    assert a["sidebar"] == "**side**" and "<strong>side</strong>" in a["sidebar_html"]
    assert a["icon"] == "https://b.thumbs.redditmedia.com/i.png"


@pytest.mark.parametrize("node,status,state", [
    ({"__typename": "UnavailableSubreddit", "forbiddenReason": "BANNED"}, 404, "banned"),
    ({"__typename": "UnavailableSubreddit", "forbiddenReason": "GOLD_ONLY"}, 403, "private"),
    ({"__typename": "UnavailableSubreddit", "forbiddenReason": "QUARANTINED"}, 403, "quarantined"),
    (None, 404, "not_found"),
])
def test_gql_about_unavailable(node, status, state):
    with patch.object(gql_subreddit, "gql_query", about_response(node)):
        with pytest.raises(UpstreamError) as e:
            fetch_gql_about("x")
    assert (e.value.status, e.value.state) == (status, state)


def test_gql_about_quarantined_and_restricted_states():
    with patch.object(gql_subreddit, "gql_query", about_response(about_node(isQuarantined=True))):
        assert fetch_gql_about("x")["state"] == "quarantined"
    with patch.object(gql_subreddit, "gql_query", about_response(about_node(type="RESTRICTED"))):
        assert fetch_gql_about("x")["state"] == "restricted"


def test_gql_rules():
    node = {"rules": [{"name": "Be nice", "content": {"markdown": "No *rudeness*"}}, {"name": "No spam", "content": None}]}
    with patch.object(gql_subreddit, "gql_query", about_response(node)):
        assert fetch_gql_rules("x") == [{"short_name": "Be nice", "description": "No *rudeness*"},
                                        {"short_name": "No spam", "description": ""}]


def test_fetch_about_falls_back_to_graphql_on_waf_block():
    canned = {"title": "T", "state": None}
    with patch.object(subreddit_routes, "reddit_get", return_value=waf_response()), \
         patch.object(subreddit_routes, "fetch_gql_about", return_value=canned):
        assert subreddit_routes.fetch_about("about_waf1") == canned


def test_fetch_about_graphql_failure_reports_the_block():
    with patch.object(subreddit_routes, "reddit_get", return_value=waf_response()), \
         patch.object(subreddit_routes, "fetch_gql_about", side_effect=RuntimeError("boom")):
        with pytest.raises(UpstreamError) as e:
            subreddit_routes.fetch_about("about_waf2")
    assert e.value.status == 403


def test_fetch_rules_falls_back_to_graphql_on_waf_block():
    rules = [{"short_name": "a", "description": "b"}]
    with patch.object(subreddit_routes, "reddit_get", return_value=waf_response()), \
         patch.object(subreddit_routes, "fetch_gql_rules", return_value=rules):
        assert subreddit_routes.fetch_rules("rules_waf1") == rules


def test_fetch_rules_still_best_effort_when_graphql_fails():
    with patch.object(subreddit_routes, "reddit_get", return_value=waf_response()), \
         patch.object(subreddit_routes, "fetch_gql_rules", side_effect=RuntimeError("boom")):
        assert subreddit_routes.fetch_rules("rules_waf2") == []


def test_csp_allows_blob_workers_for_hls():
    from app import app
    csp = app.test_client().get("/").headers["Content-Security-Policy"]
    assert "worker-src 'self' blob:" in csp


# ── popular / front page / user posts ─────────────────────────────────────────

from routes import home as home_routes, users as users_routes, search as search_routes
import gql_search


def test_cell_post_id_reads_any_cell_kind():
    cell = {"id": base64.b64encode(b"MetadataCell-t3_abc123").decode()}
    assert gql_posts._cell_post_id(cell) == "t3_abc123"
    assert gql_posts._cell_post_id({"id": "!!not base64!!"}) is None
    assert gql_posts._cell_post_id({}) is None


def listing_fake(pages, posts_by_op=None):
    """A gql_query fake: SDUI pages from `pages` in order, then PostsByIds for whatever ids come."""
    calls, queue = [], list(pages)

    def fake(op, variables, timeout=15):
        calls.append((op, variables))
        if op == "PostsByIds":
            return {"data": {"postsInfoByIds": [gql_post(i) for i in variables["ids"]]}}
        return queue.pop(0)
    fake.calls = calls
    return fake


def test_all_and_popular_use_the_popular_feed():
    page = {"data": {"popularV3": {"elements": {"edges": [{"node": {"cells": [title_cell("t3_aaa")]}}],
                                                "pageInfo": {"endCursor": None}}}}}
    for name in ("all", "popular", "ALL"):
        fake = listing_fake([page])
        with patch.object(gql_posts, "gql_query", fake):
            feed = fetch_gql_feed(name, "hot")
        assert fake.calls[0][0] == "PopularFeedSdui" and "subredditName" not in fake.calls[0][1]
        assert [p["id"] for p in feed["posts"]] == ["aaa"]


def test_frontpage_uses_popular_feed_with_sort_and_time():
    page = {"data": {"popularV3": {"elements": {"edges": [], "pageInfo": {}}}}}
    fake = listing_fake([page])
    with patch.object(gql_posts, "gql_query", fake):
        assert gql_posts.fetch_gql_frontpage("top", "week") == {"posts": [], "after": None}
    assert fake.calls[0] == ("PopularFeedSdui", {"sort": "TOP", "time": "WEEK"})


def profile_page(ids, cursor="", has_next=False):
    edges = [{"node": {"cells": [{"__typename": "MetadataCell", "id": base64.b64encode(f"MetadataCell-{i}".encode()).decode()}]}}
             for i in ids]
    return {"data": {"profileFeed": {"elements": {"edges": edges, "pageInfo": {"endCursor": cursor, "hasNextPage": has_next}}}}}


def test_user_posts_stop_when_reddit_says_there_is_no_next_page():
    # the cursor is non-empty but hasNextPage is false: it must not be followed or returned
    fake = listing_fake([profile_page(["t3_a", "t3_b"], "stale", False)])
    with patch.object(gql_posts, "gql_query", fake):
        feed = gql_posts.fetch_gql_user_posts("someone", "new")
    assert [p["id"] for p in feed["posts"]] == ["a", "b"] and feed["after"] is None
    assert fake.calls[0] == ("SubmittedPostsFeedSdui", {"username": "someone", "sort": "NEW"})
    assert len([c for c in fake.calls if c[0] == "SubmittedPostsFeedSdui"]) == 1


def test_user_posts_follow_the_cursor_and_include_profile_posts():
    fake = listing_fake([profile_page([f"t3_{i:02d}" for i in range(14)], "c1", True),
                         profile_page([f"t3_{i:02d}" for i in range(14, 28)], "c2", True)])
    with patch.object(gql_posts, "gql_query", fake):
        feed = gql_posts.fetch_gql_user_posts("someone", "top", "all")
    assert len(feed["posts"]) == 28 and feed["after"] == "c2"
    assert fake.calls[1][1]["after"] == "c1" and fake.calls[0][1]["time"] == "ALL"


def test_missing_user():
    with patch.object(gql_posts, "gql_query", lambda *a, **k: {"data": {"profileFeed": {"elements": None}}}):
        with pytest.raises(UpstreamError) as e:
            gql_posts.fetch_gql_user_posts("nobody", "new")
    assert e.value.status == 404


def test_fetch_frontpage_falls_back_on_waf_block():
    canned = {"posts": [{"id": "x"}], "after": "c"}
    with patch.object(home_routes, "reddit_get", return_value=waf_response()), \
         patch.object(home_routes, "fetch_gql_frontpage", return_value=canned), \
         patch.object(home_routes, "hydrate_linked_posts"):
        assert home_routes.fetch_frontpage("hot", "", "", ) == canned


def test_user_posts_use_graphql_before_the_archive_on_waf_block():
    canned = {"posts": [{"id": "x"}], "after": "c"}
    with patch.object(users_routes, "reddit_get", return_value=waf_response()), \
         patch.object(users_routes, "fetch_gql_user_posts", return_value=canned), \
         patch.object(users_routes, "hydrate_linked_posts"), \
         patch.object(users_routes, "_fetch_archived_posts") as archive:
        out = users_routes.fetch_user_posts("gqluser1", "new")
    assert out == {"posts": [{"id": "x"}], "after": "c"} and "archived" not in out
    archive.assert_not_called()


def test_user_posts_fall_back_to_the_archive_when_graphql_fails_too():
    with patch.object(users_routes, "reddit_get", return_value=waf_response()), \
         patch.object(users_routes, "fetch_gql_user_posts", side_effect=RuntimeError("boom")), \
         patch.object(users_routes, "_fetch_archived_posts", return_value=[]):
        with pytest.raises(UpstreamError) as e:
            users_routes.fetch_user_posts("gqluser2", "new")
    assert e.value.status == 404


# ── search ────────────────────────────────────────────────────────────────────

def search_response(nodes, cursor=None, has_next=False, key="posts"):
    edges = [{"node": n} for n in nodes]
    return lambda *a, **k: {"data": {"search": {"general": {key: {
        "edges": edges, "pageInfo": {"endCursor": cursor, "hasNextPage": has_next}}}}}}


def test_search_posts_variables_and_paging():
    seen = {}

    def fake(op, variables, timeout=15):
        seen.update(op=op, v=variables)
        return search_response([gql_post("t3_s1")], "MjQ=", True)()
    with patch.object(gql_search, "gql_query", fake):
        out = gql_search.fetch_gql_search_posts("cats", "top", "week", "aww", "MTA=")
    assert seen["op"] == "SearchPosts"
    assert seen["v"]["query"] == "subreddit:aww cats" and seen["v"]["sort"] == "TOP"
    assert seen["v"]["filters"] == [{"key": "time_range", "value": "week"}] and seen["v"]["afterCursor"] == "MTA="
    assert [p["id"] for p in out["posts"]] == ["s1"] and out["after"] == "MjQ="


def test_search_posts_last_page_has_no_cursor():
    with patch.object(gql_search, "gql_query", search_response([gql_post("t3_s1")], "stale", False)):
        assert gql_search.fetch_gql_search_posts("cats")["after"] is None


def test_search_users_carry_total_karma():
    node = {"name": "spez", "karma": {"total": 941287},
            "profile": {"createdAt": "2005-06-06T04:00:00.000000+0000",
                        "styles": {"legacyIcon": {"url": "https://styles.redditmedia.com/i.png"}}}}
    with patch.object(gql_search, "gql_query", search_response([node], key="authors")):
        out = gql_search.fetch_gql_search_users("spez")
    u = out["users"][0]
    assert (u["name"], u["karma_total"], u["icon"]) == ("spez", 941287, "https://styles.redditmedia.com/i.png")
    assert u["created_utc"] > 0


def test_search_communities():
    data = {"data": {"targetableCommunityInfo": [
        {"subredditInfo": {"name": "Python"}, "description": "Pythons", "subscriberCount": 12, "iconUrl": "https://x/i.png"},
        {"subredditInfo": None, "description": "no name", "subscriberCount": 1}]}}
    with patch.object(gql_search, "gql_query", lambda *a, **k: data):
        out = gql_search.fetch_gql_search_communities("python")
    assert out["communities"] == [{"name": "Python", "title": "Python", "description": "Pythons", "subscribers": 12,
                                   "over_18": False, "icon": "https://x/i.png"}] and out["after"] is None


def test_search_routes_fall_back_on_waf_block():
    posts = {"posts": [{"id": "p"}], "after": None}
    with patch.object(search_routes, "reddit_get", return_value=waf_response()), \
         patch.object(search_routes, "fetch_gql_search_posts", return_value=posts), \
         patch.object(search_routes, "hydrate_linked_posts"):
        assert search_routes.fetch_search_posts("waf q1") == posts
    users = {"users": [{"name": "u"}], "after": None}
    with patch.object(search_routes, "reddit_get", return_value=waf_response()), \
         patch.object(search_routes, "fetch_gql_search_users", return_value=users):
        assert search_routes.fetch_search_users("waf q2") == users
    comms = {"communities": [{"name": "c"}], "after": None}
    with patch.object(search_routes, "reddit_get", return_value=waf_response()), \
         patch.object(search_routes, "fetch_gql_search_communities", return_value=comms):
        assert search_routes.fetch_search_communities("waf q3") == comms


def test_search_posts_error_still_reported_when_graphql_fails():
    with patch.object(search_routes, "reddit_get", return_value=waf_response()), \
         patch.object(search_routes, "fetch_gql_search_posts", side_effect=RuntimeError("boom")):
        with pytest.raises(UpstreamError) as e:
            search_routes.fetch_search_posts("waf q4")
    assert e.value.status == 403


# ── single post (post page) ───────────────────────────────────────────────────

from routes import comments as comments_routes


def test_fetch_gql_post_returns_the_rest_shape():
    with patch.object(gql_posts, "gql_query", lambda op, v, t=15: {"data": {"postsInfoByIds": [gql_post("t3_one")]}}):
        post = gql_posts.fetch_gql_post("one")
    assert (post["id"], post["title"]) == ("one", "A title")


def test_fetch_gql_post_missing_or_unavailable():
    for nodes in ([], [{"__typename": "UnavailablePost", "id": "t3_x", "postTitle": None}]):
        with patch.object(gql_posts, "gql_query", lambda op, v, t=15, n=nodes: {"data": {"postsInfoByIds": n}}):
            with pytest.raises(UpstreamError) as e:
                gql_posts.fetch_gql_post("x")
        assert e.value.status == 404


def test_by_id_post_uses_rest_first():
    ok = MagicMock(status_code=200, headers={"content-type": "application/json"})
    ok.json.return_value = {"data": {"children": [{"data": {"id": "abc"}}]}}
    with patch.object(comments_routes, "reddit_get", return_value=ok), \
         patch.object(comments_routes, "fetch_gql_post", side_effect=AssertionError("REST answered")) as gql:
        assert comments_routes._by_id_post("abc") == {"id": "abc"}
    gql.assert_not_called()


def test_by_id_post_falls_back_to_graphql_on_waf_block():
    with patch.object(comments_routes, "reddit_get", return_value=waf_response()), \
         patch.object(comments_routes.time, "sleep"), \
         patch.object(comments_routes, "fetch_gql_post", return_value={"id": "abc"}):
        assert comments_routes._by_id_post("abc") == {"id": "abc"}


def test_by_id_post_graphql_failure_reports_the_block():
    with patch.object(comments_routes, "reddit_get", return_value=waf_response()), \
         patch.object(comments_routes.time, "sleep"), \
         patch.object(comments_routes, "fetch_gql_post", side_effect=RuntimeError("boom")):
        with pytest.raises(UpstreamError) as e:
            comments_routes._by_id_post("abc")
    assert e.value.status == 403


def test_by_id_post_missing_post_does_not_ask_graphql():
    gone = MagicMock(status_code=404, headers={"content-type": "application/json"})
    with patch.object(comments_routes, "reddit_get", return_value=gone), \
         patch.object(comments_routes, "fetch_gql_post", side_effect=AssertionError("a 404 is not a block")):
        with pytest.raises(UpstreamError) as e:
            comments_routes._by_id_post("abc")
    assert e.value.status == 404


# ── cursors ───────────────────────────────────────────────────────────────────

def test_cursor_conversion_round_trips_fullnames_and_leaves_opaque_cursors_alone():
    b64 = base64.b64encode(b"t3_1xiqx8c").decode()
    assert gql_posts.from_gql_cursor(b64) == "t3_1xiqx8c" and gql_posts.to_gql_cursor("t3_1xiqx8c") == b64
    assert gql_posts.from_gql_cursor("t1_abc") == "t1_abc"   # a plain fullname isn't base64: left alone
    for opaque in ("MjQ=", "YzA5YTZlMjE1MzhiNDE2ZjZhMDQ3ZjRkNGVjZDljOGU=", "c1", "", None):
        assert gql_posts.from_gql_cursor(opaque) == opaque and gql_posts.to_gql_cursor(opaque) == opaque


def test_feed_returns_rest_style_cursor_and_accepts_one_back():
    b64 = base64.b64encode(b"t3_zzz").decode()
    fake = listing_fake([profile_page([f"t3_{i:02d}" for i in range(26)], b64, True)])   # enough posts to stop paging
    with patch.object(gql_posts, "gql_query", fake):
        feed = gql_posts.fetch_gql_user_posts("someone", "new", "", "t3_prev")
    assert feed["after"] == "t3_zzz"
    assert fake.calls[0][1]["after"] == base64.b64encode(b"t3_prev").decode()


# ── user comments / overview ──────────────────────────────────────────────────

import gql_user


def comment_node(cid="t1_c1", preview="hello", **over):
    n = {"__typename": "Comment", "id": cid, "createdAt": "2026-10-29T10:00:00.000000+0000", "score": 5,
         "content": {"preview": preview},
         "postInfo": {"id": "t3_p1", "title": "A post", "subreddit": {"prefixedName": "r/pics"}}}
    n.update(over)
    return n


def comments_response(nodes, cursor=None, has_next=False, user=True):
    edges = [{"node": n} for n in nodes]
    return lambda *a, **k: {"data": {"redditorInfoByName": {"comments": {
        "edges": edges, "pageInfo": {"endCursor": cursor, "hasNextPage": has_next}}} if user else None}}


def test_user_comments_map_to_the_ui_shape():
    with patch.object(gql_user, "gql_query", comments_response([comment_node(preview="hi <b>there</b>")], None)):
        out = gql_user.fetch_gql_user_comments("someone")
    c = out["comments"][0]
    assert (c["id"], c["author"], c["subreddit"], c["link_id"], c["score"]) == ("c1", "someone", "pics", "p1", 5)
    assert c["link_title"] == "A post" and c["link_permalink"] == "https://www.reddit.com/r/pics/comments/p1/"
    assert c["body"] == "hi <b>there</b>" and "<b>" not in c["body_html"] and c["created_utc"] > 0
    assert out["after"] is None


def test_long_comment_previews_are_marked_as_cut():
    cut = "x" * 300
    with patch.object(gql_user, "gql_query", comments_response([comment_node(preview=cut), comment_node("t1_c2", "short")])):
        a, b = gql_user.fetch_gql_user_comments("u")["comments"]
    assert a["body"] == cut + "…" and b["body"] == "short"


def test_user_comments_variables_and_cursor():
    seen = {}

    def fake(op, variables, timeout=15):
        seen.update(op=op, v=variables)
        return comments_response([comment_node()], base64.b64encode(b"t1_next").decode(), True)()
    with patch.object(gql_user, "gql_query", fake):
        out = gql_user.fetch_gql_user_comments("u", "top", "week", "t1_prev")
    assert seen["op"] == "SubmittedComments"
    assert seen["v"] == {"name": "u", "sort": "TOP", "pageSize": 25, "range": "WEEK",
                         "after": base64.b64encode(b"t1_prev").decode()}
    assert out["after"] == "t1_next"
    with patch.object(gql_user, "gql_query", fake):   # the time range only applies to top
        gql_user.fetch_gql_user_comments("u", "new", "week")
    assert "range" not in seen["v"]


def test_user_comments_missing_user():
    with patch.object(gql_user, "gql_query", comments_response([], user=False)):
        with pytest.raises(UpstreamError) as e:
            gql_user.fetch_gql_user_comments("nobody")
    assert e.value.status == 404


def test_overview_merges_newest_first_and_packs_both_cursors():
    posts = {"posts": [{"id": "p_old", "created_utc": 100, "score": 1}, {"id": "p_new", "created_utc": 300, "score": 9}], "after": "t3_np"}
    comments = {"comments": [{"id": "c_mid", "created_utc": 200, "score": 50}], "after": "t1_nc"}
    with patch.object(gql_user, "fetch_gql_user_posts", return_value=posts), \
         patch.object(gql_user, "fetch_gql_user_comments", return_value=comments):
        out = gql_user.fetch_gql_user_overview("u", "new")
        assert [i["data"]["id"] for i in out["items"]] == ["p_new", "c_mid", "p_old"]
        assert [i["type"] for i in out["items"]] == ["post", "comment", "post"]
        top = gql_user.fetch_gql_user_overview("u", "top")
    assert [i["data"]["id"] for i in top["items"]] == ["c_mid", "p_new", "p_old"]   # by score
    assert out["after"].startswith("gql2:") and gql_user._decode(out["after"]) == {"p": "t3_np", "c": "t1_nc"}


def test_overview_continues_each_side_and_stops_when_both_are_done():
    after = "gql2:" + base64.urlsafe_b64encode(b'{"p": "t3_np", "c": ""}').decode()
    posts = MagicMock(return_value={"posts": [{"id": "p", "created_utc": 1}], "after": None})
    comments = MagicMock()
    with patch.object(gql_user, "fetch_gql_user_posts", posts), patch.object(gql_user, "fetch_gql_user_comments", comments):
        out = gql_user.fetch_gql_user_overview("u", "new", "", after)
    assert posts.call_args.args[3] == "t3_np"     # continued from its cursor
    comments.assert_not_called()                  # the finished side isn't asked again
    assert [i["data"]["id"] for i in out["items"]] == ["p"] and out["after"] is None


def test_user_comments_and_overview_routes_use_graphql_on_waf_block():
    comments = {"comments": [{"id": "c"}], "after": None}
    with patch.object(users_routes, "reddit_get", return_value=waf_response()), \
         patch.object(users_routes, "fetch_gql_user_comments", return_value=comments), \
         patch.object(users_routes, "_fetch_archived_comments") as archive:
        assert users_routes.fetch_user_comments("cmtuser1", "new") == comments
    archive.assert_not_called()
    overview = {"items": [{"type": "post", "data": {"id": "p"}}], "after": None}
    with patch.object(users_routes, "reddit_get", return_value=waf_response()), \
         patch.object(users_routes, "fetch_gql_user_overview", return_value=overview), \
         patch.object(users_routes, "hydrate_linked_posts"):
        assert users_routes.fetch_user_overview("ovuser1") == overview


def test_overview_gql_cursor_goes_straight_to_graphql_even_if_rest_is_back():
    overview = {"items": [], "after": None}
    with patch.object(users_routes, "reddit_get", side_effect=AssertionError("REST can't use a gql2 cursor")), \
         patch.object(users_routes, "fetch_gql_user_overview", return_value=overview), \
         patch.object(users_routes, "hydrate_linked_posts"):
        assert users_routes.fetch_user_overview("ovuser2", "new", "", "gql2:abc") == overview


def test_overview_falls_back_to_the_archive_when_graphql_fails():
    with patch.object(users_routes, "reddit_get", return_value=waf_response()), \
         patch.object(users_routes, "fetch_gql_user_overview", side_effect=RuntimeError("boom")), \
         patch.object(users_routes, "_fetch_archived_overview", return_value=([{"type": "post", "data": {}}], None)):
        out = users_routes.fetch_user_overview("ovuser3")
    assert out["archived"] is True


# ── multireddit, link hydration, avatars ──────────────────────────────────────

import helpers, routes.avatars as avatars_routes


def test_multireddit_feed_and_missing():
    page = {"data": {"customFeedV3": {"elements": {"edges": [{"node": {"cells": [title_cell("t3_aaa")]}}], "pageInfo": {}}}}}
    fake = listing_fake([page])
    with patch.object(gql_posts, "gql_query", fake):
        feed = gql_posts.fetch_gql_multireddit("kn0thing", "good", "hot")
    assert fake.calls[0] == ("CustomFeedSdui", {"path": "/user/kn0thing/m/good", "sort": "HOT"})
    assert [p["id"] for p in feed["posts"]] == ["aaa"]
    empty = {"data": {"customFeedV3": {"elements": {"edges": [], "pageInfo": {"endCursor": ""}}}}}
    with patch.object(gql_posts, "gql_query", lambda *a, **k: empty):
        with pytest.raises(UpstreamError) as e:
            gql_posts.fetch_gql_multireddit("nobody", "nothing")
        assert e.value.status == 404
        assert gql_posts.fetch_gql_multireddit("nobody", "nothing", after="t3_x")["posts"] == []   # later pages may be empty


def test_fetch_multireddit_graphql_fallback_when_rest_is_blocked():
    feed = {"posts": [{"id": "m"}], "after": None}
    with patch.object(subreddit_routes, "reddit_get", return_value=waf_response()), \
         patch.object(subreddit_routes, "fetch_gql_multireddit", return_value=feed), \
         patch.object(subreddit_routes, "hydrate_linked_posts"):
        assert subreddit_routes.fetch_multireddit("kn0thing", "good", "hot") == {**feed, "title": "good"}


def test_fetch_multireddit_rest_first_then_listing_block_keeps_the_rest_title():
    meta = MagicMock(status_code=200, headers={"content-type": "application/json"})
    meta.json.return_value = {"data": {"display_name": "Good Stuff", "subreddits": [{"name": "aww"}]}}
    feed = {"posts": [], "after": None}
    with patch.object(subreddit_routes, "reddit_get", side_effect=[meta, waf_response()]), \
         patch.object(subreddit_routes, "fetch_gql_multireddit", return_value=feed), \
         patch.object(subreddit_routes, "hydrate_linked_posts"):
        assert subreddit_routes.fetch_multireddit("kn0thing", "good", "hot")["title"] == "Good Stuff"


def test_posts_by_id_rest_first_then_graphql_on_block():
    ok = MagicMock(status_code=200, ok=True, headers={"content-type": "application/json"})
    ok.json.return_value = {"data": {"children": [{"data": {"id": "abc"}}]}}
    with patch.object(helpers, "reddit_get", return_value=ok):
        assert helpers.fetch_posts_by_id(["abc"]) == {"abc": {"id": "abc"}}
    with patch.object(helpers, "reddit_get", return_value=waf_response()), \
         patch.object(gql_posts, "fetch_gql_posts_by_id", return_value={"abc": {"id": "abc"}}) as gql:
        assert helpers.fetch_posts_by_id(["abc"]) == {"abc": {"id": "abc"}}
    gql.assert_called_once_with(["abc"])
    with patch.object(helpers, "reddit_get", return_value=waf_response()), \
         patch.object(gql_posts, "fetch_gql_posts_by_id", side_effect=RuntimeError("boom")):
        assert helpers.fetch_posts_by_id(["abc"]) == {}


def test_fetch_gql_posts_by_id_skips_unavailable_posts():
    nodes = [gql_post("t3_ok"), {"__typename": "UnavailablePost", "id": "t3_gone", "postTitle": None}]
    with patch.object(gql_posts, "gql_query", lambda *a, **k: {"data": {"postsInfoByIds": nodes}}):
        assert list(gql_posts.fetch_gql_posts_by_id(["ok", "gone"])) == ["ok"]


def test_avatar_falls_back_to_graphql_profile_on_waf_block():
    with patch.object(avatars_routes, "reddit_get", return_value=waf_response()), \
         patch.object(avatars_routes, "fetch_gql_user_about", return_value={"icon": "https://styles.redditmedia.com/a.png"}):
        assert avatars_routes._fetch_user_icon("avatar_waf_user") == "https://styles.redditmedia.com/a.png"
    with patch.object(avatars_routes, "reddit_get", return_value=waf_response()), \
         patch.object(avatars_routes, "fetch_gql_user_about", side_effect=RuntimeError("boom")):
        assert avatars_routes._fetch_user_icon("avatar_waf_user2") is None


def test_multireddit_joins_subreddits_with_an_encoded_plus():
    meta = MagicMock(status_code=200, headers={"content-type": "application/json"})
    meta.json.return_value = {"data": {"display_name": "Mix", "subreddits": [{"name": "aww"}, {"name": "pics"}]}}
    listing = MagicMock(status_code=200, headers={"content-type": "application/json"})
    listing.json.return_value = {"data": {"children": [], "after": None}}
    with patch.object(subreddit_routes, "reddit_get", side_effect=[meta, listing]) as get, \
         patch.object(subreddit_routes, "hydrate_linked_posts"):
        subreddit_routes.fetch_multireddit("someone", "mix_encoded", "hot")
    assert get.call_args_list[1].args[0] == "https://www.reddit.com/r/aww%2Bpics/hot.json"
