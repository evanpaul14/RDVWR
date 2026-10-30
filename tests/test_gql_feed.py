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
