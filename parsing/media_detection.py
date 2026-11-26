import re
import time as _time
import logging
import html as html_lib
from urllib.parse import quote as url_quote, urlparse, parse_qs
from core.base_path import app_path
from core.config import DISABLE_NSFW
from parsing.reddit_html import clean_reddit_html

log = logging.getLogger(__name__)

SELFTEXT_MAX_LEN = 600


def _vimeo_player_url(oembed):
    """Use Vimeo's responsive player instead of the fixed-size Reddit wrapper."""
    markup = html_lib.unescape(oembed.get("html") or "")
    match = re.search(r'<iframe\b[^>]*\bsrc=[\"\']([^\"\']+)', markup, re.I)
    if not match:
        return None
    src = match.group(1)
    parsed = urlparse(src)
    if parsed.hostname == "cdn.embedly.com" and parsed.path == "/widgets/media.html":
        src = parse_qs(parsed.query).get("src", [""])[0]
        parsed = urlparse(src)
    if (parsed.scheme in ("https", "") and parsed.netloc == "player.vimeo.com"
            and re.fullmatch(r"/video/\d+", parsed.path)):
        return "https:" + src if src.startswith("//") else src
    return None

def filter_nsfw(posts):
    """Drops over_18 posts when DISABLE_NSFW is set. Call on every list of processed
    posts before it reaches a client."""
    if not DISABLE_NSFW:
        return posts
    return [p for p in posts if not p.get('over_18')]

YOUTUBE_RE      = re.compile(r'(?:youtube\.com/watch.*?[?&]v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})')
REDGIFS_RE      = re.compile(r'redgifs\.com/(?:watch|ifr|embed)/([a-zA-Z0-9]+)|redgifs\.com[^"]*[?&]id=([a-zA-Z0-9]+)', re.I)
TWEET_RE        = re.compile(r'(?:twitter|x)\.com/\w+/status/(\d+)', re.I)
TIKTOK_RE       = re.compile(r'tiktok\.com/player/v1/(\d+)', re.I)
VREDDDIT_RE     = re.compile(r'(https://v\.redd\.it/[^/?]+)')
GIFV_RE         = re.compile(r'\.gifv$', re.I)
IMGUR_ALBUM_RE  = re.compile(r'imgur\.com/(?:a|gallery)/([a-zA-Z0-9]+)', re.I)
IMGUR_DIRECT_RE = re.compile(r'(?:^|/)imgur\.com/([a-zA-Z0-9]{5,9})(?:[?#]|$)', re.I)
STREAMABLE_RE   = re.compile(r'streamable\.com/(?:e/)?([a-zA-Z0-9]+)', re.I)
STREAMIN_RE     = re.compile(r'streamin\.(?:link|me)/v/([a-zA-Z0-9]+)', re.I)
DEVVIT_RE       = re.compile(r'content not supported on old Reddit.*?\((https?://sh\.reddit\.com/[^\)]+)\)', re.I | re.S)
LINK_POST_RE    = re.compile(r'^https?://(?:www\.|old\.|np\.|new\.)?reddit\.com/r/([A-Za-z0-9_]+)/comments/([a-z0-9]+)(?:/([^/?#]+))?', re.I)


def clean_url(url):
    return url.replace("&amp;", "&") if url else None


REDDIT_PREVIEW_HOSTS = ('preview.redd.it', 'cf.preview.redd.it', 'external-preview.redd.it')


def proxy_if_reddit_preview(url):
    """Wrap a preview.redd.it / external-preview.redd.it URL in our /api/img proxy
    so it loads reliably; other hosts (e.g. i.redd.it) are returned unchanged."""
    if not url:
        return url
    if (urlparse(url).hostname or '') in REDDIT_PREVIEW_HOSTS:
        return app_path(f"/api/img?url={url_quote(url, safe='')}")
    return url


def proxy_hls(url):
    """Always route v.redd.it HLS playlists through /api/m/, regardless of PROXY_MEDIA:
    hls.js loads segments/playlists via XHR, which the CSP's connect-src 'self' blocks
    for a direct cross-origin v.redd.it URL (unlike a plain <video src>, which only
    needs media-src). PROXY_MEDIA's own rewrite pass only fires for other hosts here.

    The query string is dropped: v.redd.it can 403 playlist URLs carrying Reddit's
    query (?a=...&v=1&f=sd) for minutes while the bare playlist plays fine."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.hostname == 'v.redd.it':
        return app_path(f"/api/m/{parsed.hostname}{parsed.path}")
    return url


def build_reddit_video_urls(base, cmaf=True):
    """Given a v.redd.it base URL (e.g. 'https://v.redd.it/abc123'), build the
    HLS playlist / fallback video / audio-track URLs Reddit serves under it.

    Reddit has two video encodings depending on when the video was transcoded:
    older posts use DASH_<res>.mp4 + DASH_audio.mp4, newer ones use
    CMAF_<res>.mp4 + CMAF_AUDIO_128.mp4. The legacy DASH_audio.mp4 path 403s
    for CMAF-encoded videos (and vice versa), so callers that already know
    which encoding a post uses (from its fallback_url/src) should pass
    cmaf=False to get the DASH variant; cmaf=True (the default) matches
    current uploads.
    """
    if cmaf:
        return {
            "hls_url":   base + '/HLSPlaylist.m3u8',
            "video_url": base + '/CMAF_1080.mp4',
            "audio_url": base + '/CMAF_AUDIO_128.mp4',
        }
    return {
        "hls_url":   base + '/HLSPlaylist.m3u8',
        "video_url": base + '/DASH_480.mp4',
        "audio_url": base + '/DASH_audio.mp4',
    }


def extract_redgifs_id(url):
    if not url:
        return None
    m = REDGIFS_RE.search(url)
    if not m:
        return None
    return m.group(1) or m.group(2)


def _parse_awards(awardings):
    awards = []
    for a in sorted(awardings or [], key=lambda x: -x.get("coin_price", 0)):
        resized = a.get("resized_icons") or []
        icon = clean_url(resized[0]["url"]) if resized else clean_url(
            a.get("static_icon_url") or a.get("icon_url") or "")
        if icon:
            awards.append({"name": a.get("name", ""), "count": a.get("count", 1), "icon": icon})
        if len(awards) >= 5:
            break
    return awards


def _preview_images(p):
    """(full-size preview image, ~640px card thumbnail) from Reddit's preview data, falling
    back to the post URL itself for image links that come without one."""
    preview_img = thumb_url = None
    imgs = ((p.get("preview") or {}).get("images") or [None])[0]
    if imgs and imgs.get("source"):
        preview_img = clean_url(imgs["source"]["url"])
        res = imgs.get("resolutions", [])
        # card thumbnail: smallest resolution ≥640px wide (crisp at feed-card width without
        # downloading the full-size source image, which can be 10-20x larger)
        card = next((r for r in res if r.get("width", 0) >= 640), None) or (res[-1] if res else None)
        if card:
            thumb_url = clean_url(card["url"])
    elif imgs and imgs.get("resolutions"):
        preview_img = clean_url(imgs["resolutions"][-1]["url"])

    # Fallback: NSFW/image posts often lack preview data; the URL itself is the image.
    if not preview_img and p.get("url"):
        url = p["url"]
        ext = url.lower().split("?")[0].rsplit(".", 1)[-1] if "." in url else ""
        if p.get("post_hint") == "image" or ext in {"jpg", "jpeg", "png", "webp"}:
            preview_img = clean_url(url)
    return preview_img, thumb_url


def _gallery_item(item, meta):
    """One gallery image with downscaled renditions, or None if it has no usable source."""
    mid = str(item.get("media_id", ""))
    entry = meta.get(mid, {})
    if entry.get("status") != "valid":
        if not mid or item.get("is_deleted"):
            return None
        # Reddit sometimes leaves media_metadata as {"status": "failed"} for images
        # that i.redd.it still serves; the media id is the filename (jpeg in practice).
        u = proxy_if_reddit_preview(f"https://i.redd.it/{mid}.jpeg")
        return {"url": u, "thumb": u, "mini": u, "width": 0, "height": 0,
                "caption": item.get("caption", "")}
    s = entry.get("s", {})
    url = clean_url(s.get("u") or s.get("gif"))
    if not url:
        return None
    # Downscaled renditions for feed cards / strip thumbnails, so only the post view and
    # lightbox pull the full-size original. Animated items ("gif" source) only have still
    # previews, so they keep the original.
    res = (entry.get("p") or []) if s.get("u") else []
    card = next((r for r in res if r.get("x", 0) >= 640), None) or (res[-1] if res else None)
    mini = next((r for r in res if r.get("x", 0) >= 216), None)
    return {
        "url":     proxy_if_reddit_preview(url),
        "thumb":   proxy_if_reddit_preview(clean_url(card["u"]) if card and card.get("u") else url),
        "mini":    proxy_if_reddit_preview(clean_url(mini["u"]) if mini and mini.get("u") else url),
        "width":   s.get("x", 0),
        "height":  s.get("y", 0),
        "caption": item.get("caption", ""),
    }


def _gallery(p):
    if not (p.get("is_gallery") and p.get("gallery_data") and p.get("media_metadata")):
        return []
    meta = p["media_metadata"]
    items = (_gallery_item(item, meta) for item in p["gallery_data"].get("items", []))
    return [g for g in items if g]


def _reddit_video(p, redgifs_id):
    """Reddit-hosted video fields: is_video, video_url, hls_url, audio_url, and the
    redgifs_fallback_* mirror. Skipped for redgifs posts (Reddit only mirrors their video,
    without audio), except to keep that mirror as a fallback."""
    out = {"is_video": p.get("is_video", False), "video_url": None, "hls_url": None,
           "audio_url": None, "redgifs_fallback_url": None, "redgifs_fallback_hls": None}
    rvp = (p.get("preview") or {}).get("reddit_video_preview")
    has_rvp = bool(rvp and rvp.get("fallback_url"))

    if redgifs_id:
        # Keep Reddit's mirror in case the redgifs video has since been taken down upstream.
        if has_rvp:
            out["redgifs_fallback_url"] = clean_url(rvp["fallback_url"])
            out["redgifs_fallback_hls"] = proxy_hls(clean_url(rvp.get("hls_url")))
        return out

    if out["is_video"]:
        rv = (p.get("media") or {}).get("reddit_video")
        if rv:
            out["video_url"] = clean_url(rv.get("fallback_url"))
            out["hls_url"] = proxy_hls(clean_url(rv.get("hls_url")))
    elif has_rvp:
        out["video_url"] = clean_url(rvp["fallback_url"])
        out["hls_url"] = proxy_hls(clean_url(rvp.get("hls_url")))
        out["is_video"] = True

    # Audio track for v.redd.it videos (fallback_url is video-only; audio lives in a
    # sibling file whose name depends on the video's encoding — see build_reddit_video_urls).
    video_url = out["video_url"]
    m = VREDDDIT_RE.match(video_url) if video_url and 'v.redd.it' in video_url else None
    if m:
        out["audio_url"] = build_reddit_video_urls(m.group(1), cmaf='/DASH_' not in video_url)['audio_url']
    return out


def _first_group(regex, text):
    m = regex.search(text or "")
    return m.group(1) if m else None


_NO_EMBED = {"embed_url": None, "embed_height": None, "embed_aspect_ratio": None, "tweet_id": None}


def _embed(p):
    """Generic iframe embed fields (Vimeo, tweets, other oEmbed providers)."""
    out = dict(_NO_EMBED)
    sec = p.get("secure_media_embed") or {}
    media_url = clean_url(sec.get("media_domain_url", ""))
    if not media_url:
        return out
    out["embed_url"] = media_url
    oembed = (p.get("secure_media") or {}).get("oembed") or {}
    vimeo_url = _vimeo_player_url(oembed)
    if vimeo_url:
        out["embed_url"] = vimeo_url
        try:
            width = int(oembed.get("width") or sec.get("width") or 0)
            height = int(oembed.get("height") or sec.get("height") or 0)
            if 0 < width <= 100000 and 0 < height <= 100000:
                out["embed_aspect_ratio"] = f"{width}/{height}"
        except (TypeError, ValueError, OverflowError):
            pass
    # Tweets are portrait-ish and vary in height; a 16:9 frame cuts them off.
    if "twitter" in (oembed.get("provider_name") or "").lower() or "twitter" in media_url:
        out["embed_height"] = int(sec.get("height") or oembed.get("height") or 0) or 600
        out["tweet_id"] = _first_group(TWEET_RE, p.get("url", ""))
    return out


def _gif(post_url):
    """(gif_url, gif_is_video) for direct .gif/.gifv links and bare imgur image pages."""
    lower_url = post_url.lower().split("?")[0]
    if lower_url.endswith(".gif"):
        return post_url, False
    if lower_url.endswith(".gifv"):
        return GIFV_RE.sub(".mp4", post_url), True
    imgur_id = _first_group(IMGUR_DIRECT_RE, post_url)
    return (f"https://i.imgur.com/{imgur_id}.jpg" if imgur_id else None), False


def _media(p):
    """Every media field of a post. Checked in priority order — gallery, redgifs, Reddit
    video, YouTube/Streamable/TikTok, Streamin, iframe embed, Imgur album, GIF/image —
    and a match earlier in the order suppresses the later kinds that would clash with it."""
    url = p.get("url", "")
    preview_img, thumb_url = _preview_images(p)
    gallery = _gallery(p)
    if not preview_img and gallery:
        preview_img = gallery[0]["url"]

    redgifs_id = extract_redgifs_id(url)
    media = {
        "preview_img":   proxy_if_reddit_preview(preview_img),
        "thumb_url":     proxy_if_reddit_preview(thumb_url),
        "gallery":       gallery,
        "redgifs_id":    redgifs_id,
        **_reddit_video(p, redgifs_id),
        "youtube_id":    _first_group(YOUTUBE_RE, url),
        "streamable_id": _first_group(STREAMABLE_RE, url),
        "tiktok_id":     _first_group(TIKTOK_RE, ((p.get("secure_media") or {}).get("oembed") or {}).get("html", "")),
    }

    # Streamin: real video clips (not silent loops) — play like a regular video, not an
    # autoplaying gif.
    if not (media["is_video"] or redgifs_id or media["youtube_id"]):
        streamin_id = _first_group(STREAMIN_RE, url)
        if streamin_id:
            media["is_video"] = True
            media["video_url"] = f"https://c-cdn.streamin.top/uploads/{streamin_id}.mp4"

    playable = redgifs_id or media["is_video"] or media["youtube_id"]
    has_embed_player = playable or media["tiktok_id"] or media["streamable_id"]
    media.update(_NO_EMBED if has_embed_player else _embed(p))
    media["imgur_album_id"] = None if playable else _first_group(IMGUR_ALBUM_RE, url)
    if playable or media["embed_url"] or media["imgur_album_id"]:
        media["gif_url"], media["gif_is_video"] = None, False
    else:
        media["gif_url"], media["gif_is_video"] = _gif(url)
    return media


def _poll(p):
    pd = p.get("poll_data")
    if not pd:
        return None
    return {
        "options":     [{"id": o.get("id", ""), "text": o.get("text", ""), "vote_count": o.get("vote_count")}
                        for o in pd.get("options", [])],
        "total_votes": pd.get("total_vote_count", 0),
        "closed":      pd.get("voting_end_timestamp", 0) < int(_time.time() * 1000),
    }


def _crosspost_from(p):
    """The processed original of a crosspost, or a minimal stub if it can't be processed."""
    if not p.get("crosspost_parent_list"):
        return None
    orig = dict(p["crosspost_parent_list"][0])
    orig.pop("crosspost_parent_list", None)
    try:
        return process_post(orig)
    except Exception:
        return {
            "subreddit": orig.get("subreddit", ""),
            "id":        orig.get("id", ""),
            "author":    orig.get("author", "[deleted]"),
        }


def _linked_post(p):
    """Stub for a link post that points at another Reddit post (filled in later by
    helpers.hydrate_linked_posts)."""
    m = LINK_POST_RE.match(p.get("url", "")) if not p.get("is_self") else None
    if not m:
        return None
    return {
        "subreddit": m.group(1),
        "id":        m.group(2),
        "title":     m.group(3).replace("_", " ").strip() if m.group(3) else "",
    }


def process_post(p):
    """Normalise a raw Reddit post dict into our API shape."""
    is_self = p.get("is_self", False)
    # Devvit custom posts: is_self + magic selftext string
    devvit_url = _first_group(DEVVIT_RE, p.get("selftext", "")) if is_self else None
    crosspost_from = _crosspost_from(p)
    edited = p.get("edited")
    media = _media(p)

    return {
        "id":             p.get("id", ""),
        "title":          p.get("title", ""),
        "author":         p.get("author", "[deleted]"),
        "subreddit":      p.get("subreddit", ""),
        "score":          p.get("score", 0),
        "likes":          p.get("likes"),  # the logged-in account's vote: True, False or None
        "upvote_ratio":   round(p.get("upvote_ratio", 0) * 100),
        "num_comments":   p.get("num_comments", 0),
        "created_utc":    p.get("created_utc", 0),
        "url":            p.get("url", ""),
        "permalink":      f"https://www.reddit.com{p.get('permalink', '')}",
        "is_self":        is_self,
        "selftext":       p.get("selftext", "")[:SELFTEXT_MAX_LEN] if is_self else "",
        "selftext_html":  clean_reddit_html(p.get("selftext_html")) if is_self else "",
        "preview_img":    media["preview_img"],
        "thumb_url":      media["thumb_url"],
        "gallery":        media["gallery"],
        "is_video":       media["is_video"],
        "video_url":      media["video_url"],
        "hls_url":        media["hls_url"],
        "audio_url":      media["audio_url"],
        "youtube_id":     media["youtube_id"],
        "tiktok_id":      media["tiktok_id"],
        "streamable_id":  media["streamable_id"],
        "embed_url":      media["embed_url"],
        "embed_height":   media["embed_height"],
        "embed_aspect_ratio": media["embed_aspect_ratio"],
        "tweet_id":       media["tweet_id"],
        "redgifs_id":     media["redgifs_id"],
        "redgifs_fallback_url": media["redgifs_fallback_url"],
        "redgifs_fallback_hls": media["redgifs_fallback_hls"],
        "gif_url":        media["gif_url"],
        "gif_is_video":   media["gif_is_video"],
        "imgur_album_id": media["imgur_album_id"],
        "post_hint":      p.get("post_hint", ""),
        "over_18":        p.get("over_18", False),
        "flair":          p.get("link_flair_text") or "",
        "flair_richtext": p.get("link_flair_richtext") or [],
        "flair_type":     p.get("link_flair_type", "text"),
        "flair_bg":       p.get("link_flair_background_color") or "",
        "flair_tc":       p.get("link_flair_text_color") or "dark",
        "domain":         p.get("domain", ""),
        "poll":           _poll(p),
        "crosspost_from": crosspost_from,
        "linked_post":    None if crosspost_from else _linked_post(p),
        "is_stickied":    p.get("stickied", False),
        "is_oc":          p.get("is_original_content", False),
        "is_spoiler":     p.get("spoiler", False),
        "locked":         p.get("locked", False),
        "edited_utc":     edited if isinstance(edited, (int, float)) and edited else None,
        "awards":         _parse_awards(p.get("all_awardings")),
        "is_devvit":      devvit_url is not None,
        "devvit_url":     devvit_url,
    }


def extract_posts(listing):
    posts = []
    for c in listing["children"]:
        if c.get("kind") != "t3" or c.get("data", {}).get("promoted"):
            continue
        try:
            posts.append(process_post(c["data"]))
        except (KeyError, TypeError) as e:
            log.warning("extract_posts: skipping malformed post id=%s: %s", c.get("data", {}).get("id"), e)
    return filter_nsfw(posts)
