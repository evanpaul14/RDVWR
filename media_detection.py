import os
import re
import time as _time
import logging
from urllib.parse import quote as url_quote, urlparse
from reddit_html import clean_reddit_html

log = logging.getLogger(__name__)

SELFTEXT_MAX_LEN = 600

DISABLE_NSFW = os.environ.get('DISABLE_NSFW', '0').strip().lower() in ('1', 'true', 'yes', 'on')


def filter_nsfw(posts):
    """Drop over_18 posts when DISABLE_NSFW is set; apply to every post list sent out."""
    if not DISABLE_NSFW:
        return posts
    return [p for p in posts if not p.get('over_18')]


YOUTUBE_RE      = re.compile(r'(?:youtube\.com/watch.*?[?&]v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})')
REDGIFS_RE      = re.compile(r'redgifs\.com/(?:watch|ifr|embed)/([a-zA-Z0-9]+)|redgifs\.com[^"]*[?&]id=([a-zA-Z0-9]+)', re.I)
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


REDDIT_PREVIEW_HOSTS = ('preview.redd.it', 'external-preview.redd.it')
REDDIT_IMAGE_HOSTS   = REDDIT_PREVIEW_HOSTS + ('i.redd.it',)


def gif_from_url(url):
    """(gif_url, gif_is_video) for a direct .gif/.gifv link, else (None, False)."""
    path = url.lower().split("?")[0]
    if path.endswith(".gif"):
        return url, False
    if path.endswith(".gifv"):
        return GIFV_RE.sub(".mp4", url), True
    return None, False


def parse_linked_post(url):
    """Stub {subreddit, id, title} when `url` points at another Reddit post."""
    m = LINK_POST_RE.match(url)
    if not m:
        return None
    return {
        "subreddit": m.group(1),
        "id":        m.group(2),
        "title":     m.group(3).replace("_", " ").strip() if m.group(3) else "",
    }


def proxy_if_reddit_preview(url):
    """Route preview.redd.it images through /api/img; other hosts pass through."""
    if not url:
        return url
    if (urlparse(url).hostname or '') in REDDIT_PREVIEW_HOSTS:
        return f"/api/img?url={url_quote(url, safe='')}"
    return url


def proxy_hls(url):
    """Route v.redd.it HLS through /api/m/ even without PROXY_MEDIA: hls.js fetches
    via XHR, which the CSP's connect-src 'self' would block."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.hostname == 'v.redd.it':
        qs = f"?{parsed.query}" if parsed.query else ""
        return f"/api/m/{parsed.hostname}{parsed.path}{qs}"
    return url


def build_reddit_video_urls(base, cmaf=True):
    """HLS/video/audio URLs under a v.redd.it base. Older uploads are DASH_*,
    newer are CMAF_*; the wrong variant 403s, so pass cmaf=False for DASH posts."""
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


def process_post(p):
    """Normalise a raw Reddit post dict into our API shape."""
    preview_img = None
    thumb_url = None
    if p.get("preview") and p["preview"].get("images"):
        imgs = p["preview"]["images"][0]
        if imgs.get("source"):
            preview_img = clean_url(imgs["source"]["url"])
            res = imgs.get("resolutions", [])
            # Smallest rendition ≥640px wide: crisp on cards without the full-size source.
            card = next((r for r in res if r.get("width", 0) >= 640), None) or (res[-1] if res else None)
            if card:
                thumb_url = clean_url(card["url"])
        elif imgs.get("resolutions"):
            preview_img = clean_url(imgs["resolutions"][-1]["url"])

    # Fallback: NSFW/image posts often lack preview data; the URL itself is the image.
    if not preview_img and p.get("url"):
        ext = p["url"].lower().split("?")[0].rsplit(".", 1)[-1]
        if p.get("post_hint") == "image" or ext in {"jpg", "jpeg", "png", "webp"}:
            preview_img = clean_url(p["url"])

    gallery = []
    if p.get("is_gallery") and p.get("gallery_data") and p.get("media_metadata"):
        meta = p["media_metadata"]
        for item in p["gallery_data"].get("items", []):
            mid = str(item.get("media_id", ""))
            if mid in meta and meta[mid].get("status") == "valid":
                s = meta[mid].get("s", {})
                url = clean_url(s.get("u") or s.get("gif"))
                if url:
                    # Downscaled renditions for cards/strips; animated items have only
                    # still previews, so they keep the original.
                    res = (meta[mid].get("p") or []) if s.get("u") else []
                    card = next((r for r in res if r.get("x", 0) >= 640), None) or (res[-1] if res else None)
                    mini = next((r for r in res if r.get("x", 0) >= 216), None)
                    gallery.append({
                        "url":     proxy_if_reddit_preview(url),
                        "thumb":   proxy_if_reddit_preview(clean_url(card["u"]) if card and card.get("u") else url),
                        "mini":    proxy_if_reddit_preview(clean_url(mini["u"]) if mini and mini.get("u") else url),
                        "width":   s.get("x", 0),
                        "height":  s.get("y", 0),
                        "caption": item.get("caption", ""),
                    })
    if not preview_img and gallery:
        preview_img = gallery[0]["url"]

    preview_img = proxy_if_reddit_preview(preview_img)
    thumb_url = proxy_if_reddit_preview(thumb_url)

    post_url = p.get("url", "")
    # Found first so Reddit's silent mirror of the clip isn't used as the main video.
    redgifs_id = extract_redgifs_id(post_url)

    is_video  = p.get("is_video", False)
    video_url = hls_url = None
    if not redgifs_id and is_video and p.get("media") and (p["media"] or {}).get("reddit_video"):
        rv        = p["media"]["reddit_video"]
        video_url = clean_url(rv.get("fallback_url"))
        hls_url   = proxy_hls(clean_url(rv.get("hls_url")))

    if not redgifs_id and not is_video:
        rvp = (p.get("preview") or {}).get("reddit_video_preview")
        if rvp and rvp.get("fallback_url"):
            video_url = clean_url(rvp["fallback_url"])
            hls_url   = proxy_hls(clean_url(rvp.get("hls_url")))
            is_video  = True

    # Reddit's silent mirror, kept as a fallback if the original is taken down.
    redgifs_fallback_url = None
    redgifs_fallback_hls = None
    if redgifs_id:
        rvp = (p.get("preview") or {}).get("reddit_video_preview")
        if rvp and rvp.get("fallback_url"):
            redgifs_fallback_url = clean_url(rvp["fallback_url"])
            redgifs_fallback_hls = proxy_hls(clean_url(rvp.get("hls_url")))

    # fallback_url is video-only; audio is a sibling file named per encoding.
    audio_url = None
    if video_url and 'v.redd.it' in video_url:
        m = VREDDDIT_RE.match(video_url)
        if m:
            audio_url = build_reddit_video_urls(m.group(1), cmaf='/DASH_' not in video_url)['audio_url']

    yt = YOUTUBE_RE.search(post_url)
    youtube_id = yt.group(1) if yt else None
    sm = STREAMABLE_RE.search(post_url)
    streamable_id = sm.group(1) if sm else None
    oembed_html = ((p.get("secure_media") or {}).get("oembed") or {}).get("html", "")
    tt = TIKTOK_RE.search(oembed_html)
    tiktok_id = tt.group(1) if tt else None

    # Streamin clips have sound, so play them as regular video rather than a gif loop.
    if not is_video and not redgifs_id and not youtube_id:
        m = STREAMIN_RE.search(post_url)
        if m:
            is_video  = True
            video_url = f"https://c-cdn.streamin.top/uploads/{m.group(1)}.mp4"

    embed_url = None
    if not redgifs_id and not is_video and not youtube_id and not tiktok_id and not streamable_id:
        embed_url = clean_url((p.get("secure_media_embed") or {}).get("media_domain_url", "")) or None

    imgur_album_id = None
    if not redgifs_id and not is_video and not youtube_id:
        m = IMGUR_ALBUM_RE.search(post_url)
        if m:
            imgur_album_id = m.group(1)

    gif_url = None
    gif_is_video = False
    if not is_video and not redgifs_id and not youtube_id and not embed_url and not imgur_album_id:
        gif_url, gif_is_video = gif_from_url(post_url)
        if not gif_url:
            m = IMGUR_DIRECT_RE.search(post_url)
            if m:
                gif_url = f"https://i.imgur.com/{m.group(1)}.jpg"

    # Devvit custom posts are self posts with a placeholder selftext linking to sh.reddit.
    m = DEVVIT_RE.search(p.get("selftext", "")) if p.get("is_self") else None
    devvit_url = m.group(1) if m else None

    poll = None
    if p.get("poll_data"):
        pd = p["poll_data"]
        poll = {
            "options":      [{"id": o.get("id", ""), "text": o.get("text", ""), "vote_count": o.get("vote_count")} for o in pd.get("options", [])],
            "total_votes":  pd.get("total_vote_count", 0),
            "closed":       pd.get("voting_end_timestamp", 0) < int(_time.time() * 1000),
        }

    awards = _parse_awards(p.get("all_awardings"))

    crosspost_from = None
    if p.get("crosspost_parent_list"):
        orig = dict(p["crosspost_parent_list"][0])
        orig.pop("crosspost_parent_list", None)
        try:
            crosspost_from = process_post(orig)
        except Exception:
            crosspost_from = {
                "subreddit": orig.get("subreddit", ""),
                "id":        orig.get("id", ""),
                "author":    orig.get("author", "[deleted]"),
            }

    linked_post = None
    if not crosspost_from and not p.get("is_self"):
        linked_post = parse_linked_post(post_url)

    edited = p.get("edited")
    edited_utc = edited if isinstance(edited, (int, float)) and edited else None

    selftext_html = clean_reddit_html(p.get("selftext_html")) if p.get("is_self") else ""

    return {
        "id":             p.get("id", ""),
        "title":          p.get("title", ""),
        "author":         p.get("author", "[deleted]"),
        "subreddit":      p.get("subreddit", ""),
        "score":          p.get("score", 0),
        "upvote_ratio":   round(p.get("upvote_ratio", 0) * 100),
        "num_comments":   p.get("num_comments", 0),
        "created_utc":    p.get("created_utc", 0),
        "url":            post_url,
        "permalink":      f"https://www.reddit.com{p.get('permalink', '')}",
        "is_self":        p.get("is_self", False),
        "selftext":       p.get("selftext", "")[:SELFTEXT_MAX_LEN] if p.get("is_self") else "",
        "selftext_html":  selftext_html,
        "preview_img":    preview_img,
        "thumb_url":      thumb_url,
        "gallery":        gallery,
        "is_video":       is_video,
        "video_url":      video_url,
        "hls_url":        hls_url,
        "audio_url":      audio_url,
        "youtube_id":     youtube_id,
        "tiktok_id":      tiktok_id,
        "streamable_id":  streamable_id,
        "embed_url":      embed_url,
        "redgifs_id":     redgifs_id,
        "redgifs_fallback_url": redgifs_fallback_url,
        "redgifs_fallback_hls": redgifs_fallback_hls,
        "gif_url":        gif_url,
        "gif_is_video":   gif_is_video,
        "imgur_album_id": imgur_album_id,
        "post_hint":      p.get("post_hint", ""),
        "over_18":        p.get("over_18", False),
        "flair":          p.get("link_flair_text") or "",
        "flair_richtext": p.get("link_flair_richtext") or [],
        "flair_type":     p.get("link_flair_type", "text"),
        "flair_bg":       p.get("link_flair_background_color") or "",
        "flair_tc":       p.get("link_flair_text_color") or "dark",
        "domain":         p.get("domain", ""),
        "poll":           poll,
        "crosspost_from": crosspost_from,
        "linked_post":    linked_post,
        "is_stickied":    p.get("stickied", False),
        "is_oc":          p.get("is_original_content", False),
        "is_spoiler":     p.get("spoiler", False),
        "locked":         p.get("locked", False),
        "edited_utc":     edited_utc,
        "awards":         awards,
        "is_devvit":      bool(devvit_url),
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
