import re
import time as _time
import logging
from urllib.parse import quote as url_quote, urlparse

log = logging.getLogger(__name__)

SELFTEXT_MAX_LEN = 600

YOUTUBE_RE      = re.compile(r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})')
REDGIFS_RE      = re.compile(r'redgifs\.com/(?:watch|ifr|embed)/([a-zA-Z0-9]+)|redgifs\.com[^"]*[?&]id=([a-zA-Z0-9]+)', re.I)
TIKTOK_RE       = re.compile(r'tiktok\.com/player/v1/(\d+)', re.I)
VREDDDIT_RE     = re.compile(r'(https://v\.redd\.it/[^/?]+)')
GIFV_RE         = re.compile(r'\.gifv$', re.I)
IMGUR_ALBUM_RE  = re.compile(r'imgur\.com/(?:a|gallery)/([a-zA-Z0-9]+)', re.I)
IMGUR_DIRECT_RE = re.compile(r'(?:^|/)imgur\.com/([a-zA-Z0-9]{5,9})(?:[?#]|$)', re.I)
STREAMABLE_RE   = re.compile(r'streamable\.com/(?:e/)?([a-zA-Z0-9]+)', re.I)


def clean_url(url):
    return url.replace("&amp;", "&") if url else None


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
    if p.get("preview") and p["preview"].get("images"):
        imgs = p["preview"]["images"][0]
        if imgs.get("source"):
            preview_img = clean_url(imgs["source"]["url"])
        elif imgs.get("resolutions"):
            preview_img = clean_url(imgs["resolutions"][-1]["url"])

    # Fallback: NSFW/image posts often lack preview data; the URL itself is the image.
    if not preview_img and p.get("url"):
        _pu = p["url"]
        _ext = _pu.lower().split("?")[0].rsplit(".", 1)[-1] if "." in _pu else ""
        if p.get("post_hint") == "image" or _ext in {"jpg", "jpeg", "png", "webp"}:
            preview_img = clean_url(_pu)

    gallery = []
    if p.get("is_gallery") and p.get("gallery_data") and p.get("media_metadata"):
        meta = p.get("media_metadata", {})
        for item in p["gallery_data"].get("items", []):
            mid = str(item.get("media_id", ""))
            if mid in meta and meta[mid].get("status") == "valid":
                s = meta[mid].get("s", {})
                url = clean_url(s.get("u") or s.get("gif"))
                if url:
                    gallery.append({
                        "url":     url,
                        "width":   s.get("x", 0),
                        "height":  s.get("y", 0),
                        "caption": item.get("caption", ""),
                    })
    if not preview_img and gallery:
        preview_img = gallery[0]["url"]

    # Proxy preview.redd.it / external-preview.redd.it images through backend so they load reliably
    if preview_img:
        _ph = urlparse(preview_img).hostname or ''
        if _ph in ('preview.redd.it', 'external-preview.redd.it'):
            preview_img = f"/api/img?url={url_quote(preview_img, safe='')}"

    # RedGifs: extract ID from post URL early so we skip Reddit's video-only preview
    redgifs_id = extract_redgifs_id(p.get("url", ""))

    # Reddit-hosted video (skip for redgifs — Reddit only mirrors video, no audio)
    is_video  = p.get("is_video", False)
    video_url = hls_url = None
    if not redgifs_id and is_video and p.get("media") and (p["media"] or {}).get("reddit_video"):
        rv        = p["media"]["reddit_video"]
        video_url = clean_url(rv.get("fallback_url"))
        hls_url   = clean_url(rv.get("hls_url"))

    # reddit_video_preview — skip for redgifs (same reason)
    if not redgifs_id and not is_video:
        rvp = (p.get("preview") or {}).get("reddit_video_preview")
        if rvp and rvp.get("fallback_url"):
            video_url = clean_url(rvp["fallback_url"])
            hls_url   = clean_url(rvp.get("hls_url"))
            is_video  = True

    # Audio track for v.redd.it videos (fallback_url is video-only; audio lives at DASH_audio.mp4)
    audio_url = None
    if video_url and 'v.redd.it' in video_url:
        m = VREDDDIT_RE.match(video_url)
        if m:
            audio_url = m.group(1) + '/DASH_audio.mp4'

    youtube_id = None
    yt = YOUTUBE_RE.search(p.get("url", ""))
    if yt:
        youtube_id = yt.group(1)

    streamable_id = None
    sm = STREAMABLE_RE.search(p.get("url", ""))
    if sm:
        streamable_id = sm.group(1)

    tiktok_id = None
    oembed_html = ((p.get("secure_media") or {}).get("oembed") or {}).get("html", "")
    tt = TIKTOK_RE.search(oembed_html)
    if tt:
        tiktok_id = tt.group(1)

    # Generic iframe embed (non-redgifs, non-reddit, non-youtube, non-tiktok, non-streamable)
    embed_url = None
    if not redgifs_id and not is_video and not youtube_id and not tiktok_id and not streamable_id:
        sec       = p.get("secure_media_embed") or {}
        media_url = clean_url(sec.get("media_domain_url", ""))
        if media_url:
            embed_url = media_url

    imgur_album_id = None
    if not redgifs_id and not is_video and not youtube_id:
        m = IMGUR_ALBUM_RE.search(p.get("url", ""))
        if m:
            imgur_album_id = m.group(1)

    gif_url = None
    gif_is_video = False
    if not is_video and not redgifs_id and not youtube_id and not embed_url and not imgur_album_id:
        post_url  = p.get("url", "")
        lower_url = post_url.lower().split("?")[0]
        if lower_url.endswith(".gif"):
            gif_url = post_url
        elif lower_url.endswith(".gifv"):
            gif_url      = GIFV_RE.sub(".mp4", post_url)
            gif_is_video = True
        else:
            m = IMGUR_DIRECT_RE.search(post_url)
            if m:
                gif_url = f"https://i.imgur.com/{m.group(1)}.jpg"

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
        except Exception as e:
            log.debug("crosspost_from parse failed id=%s: %s", orig.get("id"), e)
            crosspost_from = {
                "subreddit": orig.get("subreddit", ""),
                "id":        orig.get("id", ""),
                "author":    orig.get("author", "[deleted]"),
            }

    edited = p.get("edited")
    edited_utc = edited if isinstance(edited, (int, float)) and edited else None

    return {
        "id":             p["id"],
        "title":          p["title"],
        "author":         p.get("author", "[deleted]"),
        "subreddit":      p["subreddit"],
        "score":          p.get("score", 0),
        "upvote_ratio":   round(p.get("upvote_ratio", 0) * 100),
        "num_comments":   p.get("num_comments", 0),
        "created_utc":    p.get("created_utc", 0),
        "url":            p.get("url", ""),
        "permalink":      f"https://www.reddit.com{p.get('permalink', '')}",
        "is_self":        p.get("is_self", False),
        "selftext":       p.get("selftext", "")[:SELFTEXT_MAX_LEN] if p.get("is_self") else "",
        "preview_img":    preview_img,
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
        "is_stickied":    p.get("stickied", False),
        "is_oc":          p.get("is_original_content", False),
        "is_spoiler":     p.get("spoiler", False),
        "locked":         p.get("locked", False),
        "edited_utc":     edited_utc,
        "awards":         awards,
    }


def extract_posts(listing):
    return [process_post(c["data"]) for c in listing["children"]
            if c.get("kind") == "t3" and not c.get("data", {}).get("promoted")]
