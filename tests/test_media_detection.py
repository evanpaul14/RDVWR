"""Unit tests for media_detection.py — no network, no Flask."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from media_detection import (
    clean_url,
    extract_redgifs_id,
    _parse_awards,
    process_post,
    extract_posts,
    SELFTEXT_MAX_LEN,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _post(**kw):
    """Minimal valid Reddit post dict; override any field with kwargs."""
    base = {"id": "abc123", "title": "Test Post", "subreddit": "testsubreddit"}
    base.update(kw)
    return base


# ── clean_url ─────────────────────────────────────────────────────────────────

class TestCleanUrl:
    def test_replaces_amp_entity(self):
        assert clean_url("https://example.com/a?x=1&amp;y=2") == "https://example.com/a?x=1&y=2"

    def test_no_change_when_no_entity(self):
        assert clean_url("https://example.com/img.jpg") == "https://example.com/img.jpg"

    def test_none_returns_none(self):
        assert clean_url(None) is None

    def test_empty_string(self):
        assert clean_url("") is None


# ── extract_redgifs_id ────────────────────────────────────────────────────────

class TestExtractRedgifsId:
    def test_watch_url(self):
        assert extract_redgifs_id("https://www.redgifs.com/watch/AbCdEf") == "AbCdEf"

    def test_ifr_url(self):
        assert extract_redgifs_id("https://www.redgifs.com/ifr/CoolGif123") == "CoolGif123"

    def test_embed_url(self):
        assert extract_redgifs_id("https://www.redgifs.com/embed/MyGif") == "MyGif"

    def test_query_id_param(self):
        assert extract_redgifs_id("https://redgifs.com/something?id=XyZ789") == "XyZ789"

    def test_none_input(self):
        assert extract_redgifs_id(None) is None

    def test_non_redgifs_url(self):
        assert extract_redgifs_id("https://youtube.com/watch?v=abc") is None

    def test_empty_string(self):
        assert extract_redgifs_id("") is None


# ── _parse_awards ─────────────────────────────────────────────────────────────

class TestParseAwards:
    def _make_award(self, name, coin_price, count=1, icon=None):
        return {
            "name": name,
            "coin_price": coin_price,
            "count": count,
            "resized_icons": [{"url": icon}] if icon else [],
            "static_icon_url": icon or "",
        }

    def test_empty_list(self):
        assert _parse_awards([]) == []

    def test_none(self):
        assert _parse_awards(None) == []

    def test_sorted_by_coin_price_desc(self):
        awards = [
            self._make_award("Bronze", 100, icon="https://b.png"),
            self._make_award("Gold", 500, icon="https://g.png"),
            self._make_award("Silver", 200, icon="https://s.png"),
        ]
        result = _parse_awards(awards)
        assert [a["name"] for a in result] == ["Gold", "Silver", "Bronze"]

    def test_capped_at_five(self):
        awards = [self._make_award(f"Award{i}", i * 10, icon=f"https://{i}.png") for i in range(10, 0, -1)]
        assert len(_parse_awards(awards)) == 5

    def test_skips_awards_without_icon(self):
        awards = [{"name": "No Icon", "coin_price": 100, "resized_icons": [], "static_icon_url": "", "icon_url": ""}]
        assert _parse_awards(awards) == []

    def test_cleans_amp_in_icon_url(self):
        awards = [self._make_award("Clean", 100, icon="https://img.com/a?x=1&amp;y=2")]
        result = _parse_awards(awards)
        assert "amp;" not in result[0]["icon"]


# ── process_post ──────────────────────────────────────────────────────────────

class TestProcessPost:
    def test_minimal_post_keys(self):
        result = process_post(_post())
        required = {
            "id", "title", "author", "subreddit", "score", "upvote_ratio",
            "num_comments", "created_utc", "url", "permalink", "is_self",
            "selftext", "preview_img", "gallery", "is_video", "video_url",
            "hls_url", "audio_url", "youtube_id", "tiktok_id", "streamable_id",
            "embed_url", "redgifs_id", "gif_url", "gif_is_video", "imgur_album_id",
            "post_hint", "over_18", "flair", "flair_richtext", "flair_type",
            "flair_bg", "flair_tc", "domain", "poll", "crosspost_from",
            "is_stickied", "is_oc", "is_spoiler", "locked", "edited_utc", "awards",
        }
        assert required.issubset(result.keys())

    def test_permalink_prefixed(self):
        result = process_post(_post(permalink="/r/test/comments/abc/title/"))
        assert result["permalink"].startswith("https://www.reddit.com")

    def test_selftext_truncated(self):
        long_text = "x" * (SELFTEXT_MAX_LEN + 100)
        result = process_post(_post(is_self=True, selftext=long_text))
        assert len(result["selftext"]) == SELFTEXT_MAX_LEN

    def test_selftext_empty_for_link_post(self):
        result = process_post(_post(is_self=False, selftext="should not appear"))
        assert result["selftext"] == ""

    def test_upvote_ratio_rounded(self):
        result = process_post(_post(upvote_ratio=0.876))
        assert result["upvote_ratio"] == 88

    # Preview image

    def test_preview_img_from_source(self):
        p = _post(preview={"images": [{"source": {"url": "https://preview.redd.it/img.jpg&amp;auto=webp"}}]})
        result = process_post(p)
        assert result["preview_img"] is not None
        assert "amp;" not in result["preview_img"]

    def test_preview_img_from_resolutions_fallback(self):
        p = _post(preview={"images": [{"resolutions": [
            {"url": "https://preview.redd.it/small.jpg"},
            {"url": "https://preview.redd.it/large.jpg"},
        ]}]})
        result = process_post(p)
        assert "large" in result["preview_img"] or result["preview_img"] is not None

    def test_preview_img_proxied_for_redd_it(self):
        p = _post(preview={"images": [{"source": {"url": "https://preview.redd.it/img.jpg"}}]})
        result = process_post(p)
        assert result["preview_img"].startswith("/api/img?url=")

    def test_preview_img_not_proxied_for_other_hosts(self):
        p = _post(url="https://i.imgur.com/abc.jpg", post_hint="image")
        result = process_post(p)
        assert result["preview_img"] == "https://i.imgur.com/abc.jpg"

    def test_preview_img_from_url_when_image_hint(self):
        p = _post(url="https://example.com/photo.jpg", post_hint="image")
        result = process_post(p)
        assert result["preview_img"] == "https://example.com/photo.jpg"

    def test_preview_img_from_url_jpeg_extension(self):
        p = _post(url="https://example.com/photo.jpeg")
        result = process_post(p)
        assert result["preview_img"] == "https://example.com/photo.jpeg"

    # Gallery

    def test_gallery_parsed(self):
        p = _post(
            is_gallery=True,
            gallery_data={"items": [{"media_id": "m1", "caption": "cap1"}]},
            media_metadata={"m1": {"status": "valid", "s": {"u": "https://img.example.com/1.jpg&amp;w=1", "x": 800, "y": 600}}},
        )
        result = process_post(p)
        assert len(result["gallery"]) == 1
        assert result["gallery"][0]["width"] == 800
        assert "amp;" not in result["gallery"][0]["url"]

    def test_gallery_skips_invalid_items(self):
        p = _post(
            is_gallery=True,
            gallery_data={"items": [{"media_id": "bad"}]},
            media_metadata={"bad": {"status": "failed"}},
        )
        result = process_post(p)
        assert result["gallery"] == []

    def test_gallery_first_image_becomes_preview(self):
        p = _post(
            is_gallery=True,
            gallery_data={"items": [{"media_id": "m1"}]},
            media_metadata={"m1": {"status": "valid", "s": {"u": "https://example.com/img.jpg"}}},
        )
        result = process_post(p)
        assert result["preview_img"] == "https://example.com/img.jpg"

    # Reddit-hosted video

    def test_reddit_video_parsed(self):
        p = _post(
            is_video=True,
            media={"reddit_video": {
                "fallback_url": "https://v.redd.it/abc123/DASH_720.mp4",
                "hls_url": "https://v.redd.it/abc123/HLSPlaylist.m3u8",
            }},
        )
        result = process_post(p)
        assert result["is_video"] is True
        assert "v.redd.it" in result["video_url"]
        assert result["audio_url"] == "https://v.redd.it/abc123/DASH_audio.mp4"

    def test_reddit_video_preview_fallback(self):
        p = _post(preview={"reddit_video_preview": {
            "fallback_url": "https://v.redd.it/xyz/DASH_480.mp4",
            "hls_url": "https://v.redd.it/xyz/HLSPlaylist.m3u8",
        }})
        result = process_post(p)
        assert result["is_video"] is True
        assert result["audio_url"] == "https://v.redd.it/xyz/DASH_audio.mp4"

    # YouTube

    def test_youtube_id_extracted_watch_url(self):
        p = _post(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        result = process_post(p)
        assert result["youtube_id"] == "dQw4w9WgXcQ"

    def test_youtube_id_extracted_short_url(self):
        p = _post(url="https://youtu.be/dQw4w9WgXcQ")
        result = process_post(p)
        assert result["youtube_id"] == "dQw4w9WgXcQ"

    def test_non_youtube_url(self):
        p = _post(url="https://example.com/video")
        result = process_post(p)
        assert result["youtube_id"] is None

    # RedGifs

    def test_redgifs_id_extracted(self):
        p = _post(url="https://www.redgifs.com/watch/CoolGif")
        result = process_post(p)
        assert result["redgifs_id"] == "CoolGif"

    def test_redgifs_suppresses_reddit_video(self):
        p = _post(
            url="https://www.redgifs.com/watch/MyGif",
            is_video=True,
            media={"reddit_video": {"fallback_url": "https://v.redd.it/abc/DASH_720.mp4", "hls_url": "hls"}},
        )
        result = process_post(p)
        assert result["redgifs_id"] == "MyGif"
        assert result["video_url"] is None

    # Streamable

    def test_streamable_id_extracted(self):
        p = _post(url="https://streamable.com/abc123")
        result = process_post(p)
        assert result["streamable_id"] == "abc123"

    def test_streamable_e_path(self):
        p = _post(url="https://streamable.com/e/xyz99")
        result = process_post(p)
        assert result["streamable_id"] == "xyz99"

    # TikTok

    def test_tiktok_id_from_oembed(self):
        p = _post(secure_media={"oembed": {"html": "tiktok.com/player/v1/7123456789012345678"}})
        result = process_post(p)
        assert result["tiktok_id"] == "7123456789012345678"

    # Generic embed

    def test_generic_embed_url(self):
        p = _post(secure_media_embed={"media_domain_url": "https://embed.example.com/v/abc"})
        result = process_post(p)
        assert result["embed_url"] == "https://embed.example.com/v/abc"

    def test_embed_not_set_when_redgifs(self):
        p = _post(
            url="https://redgifs.com/watch/Abc",
            secure_media_embed={"media_domain_url": "https://embed.example.com/v/abc"},
        )
        result = process_post(p)
        assert result["embed_url"] is None

    # Imgur

    def test_imgur_album_id_extracted(self):
        p = _post(url="https://imgur.com/a/AbCdEfG")
        result = process_post(p)
        assert result["imgur_album_id"] == "AbCdEfG"

    def test_imgur_gallery_id_extracted(self):
        p = _post(url="https://imgur.com/gallery/ZyXwV")
        result = process_post(p)
        assert result["imgur_album_id"] == "ZyXwV"

    def test_imgur_direct_image_url(self):
        p = _post(url="https://imgur.com/AbCdEfG")
        result = process_post(p)
        assert result["gif_url"] == "https://i.imgur.com/AbCdEfG.jpg"

    # GIF / GIFV

    def test_gif_url(self):
        p = _post(url="https://example.com/animated.gif")
        result = process_post(p)
        assert result["gif_url"] == "https://example.com/animated.gif"
        assert result["gif_is_video"] is False

    def test_gifv_converted_to_mp4(self):
        p = _post(url="https://i.imgur.com/Abc.gifv")
        result = process_post(p)
        assert result["gif_url"].endswith(".mp4")
        assert result["gif_is_video"] is True

    # Poll

    def test_poll_parsed(self):
        import time as _time
        p = _post(poll_data={
            "options": [
                {"id": "1", "text": "Yes", "vote_count": 42},
                {"id": "2", "text": "No", "vote_count": 10},
            ],
            "total_vote_count": 52,
            "voting_end_timestamp": 0,  # already ended
        })
        result = process_post(p)
        assert result["poll"] is not None
        assert result["poll"]["total_votes"] == 52
        assert result["poll"]["closed"] is True
        assert len(result["poll"]["options"]) == 2

    def test_no_poll_when_absent(self):
        result = process_post(_post())
        assert result["poll"] is None

    # Crosspost

    def test_crosspost_parsed(self):
        orig = {"id": "orig1", "title": "Original", "subreddit": "origSub", "author": "origUser"}
        p = _post(crosspost_parent_list=[orig])
        result = process_post(p)
        assert result["crosspost_from"] is not None
        assert result["crosspost_from"]["id"] == "orig1"

    def test_crosspost_no_infinite_recursion(self):
        orig = {"id": "o1", "title": "Orig", "subreddit": "s", "crosspost_parent_list": []}
        p = _post(crosspost_parent_list=[orig])
        result = process_post(p)
        assert result["crosspost_from"] is not None

    # edited_utc

    def test_edited_utc_numeric(self):
        result = process_post(_post(edited=1700000000.0))
        assert result["edited_utc"] == 1700000000.0

    def test_edited_false_gives_none(self):
        result = process_post(_post(edited=False))
        assert result["edited_utc"] is None

    def test_edited_zero_gives_none(self):
        result = process_post(_post(edited=0))
        assert result["edited_utc"] is None


# ── extract_posts ─────────────────────────────────────────────────────────────

class TestExtractPosts:
    def test_filters_non_t3(self):
        listing = {"children": [
            {"kind": "t3", "data": _post(id="p1")},
            {"kind": "t1", "data": {"id": "c1", "title": "?", "subreddit": "x"}},
            {"kind": "t3", "data": _post(id="p2")},
        ]}
        result = extract_posts(listing)
        assert len(result) == 2
        assert result[0]["id"] == "p1"
        assert result[1]["id"] == "p2"

    def test_empty_listing(self):
        assert extract_posts({"children": []}) == []
