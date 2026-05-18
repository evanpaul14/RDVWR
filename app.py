import os
import re
import json
import html as html_lib
import time
import requests
from flask import Flask, render_template, jsonify, request, Response, make_response

CACHE_TTL_STATIC     = 604800   # 1 week
CACHE_TTL_FEED       = 300
CACHE_TTL_SUBREDDIT  = 600
REDGIFS_TOKEN_TTL    = 23 * 3600
SELFTEXT_MAX_LEN     = 600
FEED_LIMIT           = 25
COMMENTS_LIMIT       = 200
STREAM_CHUNK_SIZE    = 65536

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = CACHE_TTL_STATIC
HEADERS    = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"}
YOUTUBE_RE = re.compile(r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})')
REDGIFS_RE = re.compile(r'redgifs\.com/(?:watch|ifr|embed)/([a-zA-Z0-9]+)|redgifs\.com[^"]*[?&]id=([a-zA-Z0-9]+)', re.I)
TIKTOK_RE           = re.compile(r'tiktok\.com/player/v1/(\d+)', re.I)
VREDDDIT_RE         = re.compile(r'(https://v\.redd\.it/[^/?]+)')
REDGIFS_ID_VALID_RE = re.compile(r'^[a-zA-Z0-9]+$')
GIFV_RE             = re.compile(r'\.gifv$', re.I)
IMGUR_ALBUM_RE      = re.compile(r'imgur\.com/(?:a|gallery)/([a-zA-Z0-9]+)', re.I)
IMGUR_DIRECT_RE     = re.compile(r'(?:^|/)imgur\.com/([a-zA-Z0-9]{5,9})(?:[?#]|$)', re.I)
IMGUR_ALBUM_ID_RE   = re.compile(r'^[a-zA-Z0-9]+$')
IMGUR_CLIENT_ID     = os.environ.get('IMGUR_CLIENT_ID', '')
IMGUR_IMG_URL_RE    = re.compile(r'https://i\.imgur\.com/([A-Za-z0-9]{5,9})\.(jpe?g|png|gif|webp)', re.I)
_IMGUR_THUMB_CHARS  = frozenset('smbtlr')

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

_rg_token     = None
_rg_token_exp = 0.0

def cached_json(data, seconds):
    resp = make_response(jsonify(data))
    resp.headers['Cache-Control'] = f'public, max-age={seconds}'
    return resp

def get_redgifs_token():
    global _rg_token, _rg_token_exp
    if _rg_token and time.time() < _rg_token_exp:
        return _rg_token
    r = SESSION.get("https://api.redgifs.com/v2/auth/temporary", timeout=10)
    r.raise_for_status()
    _rg_token     = r.json()["token"]
    _rg_token_exp = time.time() + REDGIFS_TOKEN_TTL
    return _rg_token

def extract_redgifs_id(url):
    if not url:
        return None
    m = REDGIFS_RE.search(url)
    if not m:
        return None
    return m.group(1) or m.group(2)


def clean_url(url):
    return url.replace("&amp;", "&") if url else None


def process_post(p):
    """Normalise a raw Reddit post dict into our API shape."""
    # Preview image (highest res source)
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

    # Gallery (ordered)
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

    # YouTube
    youtube_id = None
    yt = YOUTUBE_RE.search(p.get("url", ""))
    if yt:
        youtube_id = yt.group(1)

    # TikTok — extract player video ID from oembed HTML
    tiktok_id = None
    oembed_html = ((p.get("secure_media") or {}).get("oembed") or {}).get("html", "")
    tt = TIKTOK_RE.search(oembed_html)
    if tt:
        tiktok_id = tt.group(1)

    # Generic iframe embed (non-redgifs, non-reddit, non-youtube, non-tiktok)
    embed_url = None
    if not redgifs_id and not is_video and not youtube_id and not tiktok_id:
        sec       = p.get("secure_media_embed") or {}
        media_url = clean_url(sec.get("media_domain_url", ""))
        if media_url:
            embed_url = media_url

    # Imgur album/gallery
    imgur_album_id = None
    if not redgifs_id and not is_video and not youtube_id:
        m = IMGUR_ALBUM_RE.search(p.get("url", ""))
        if m:
            imgur_album_id = m.group(1)

    # Direct GIF / GIFV URLs not already captured as video
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

    # Poll data
    poll = None
    if p.get("poll_data"):
        pd = p["poll_data"]
        poll = {
            "options":      [{"id": o.get("id", ""), "text": o.get("text", ""), "vote_count": o.get("vote_count")} for o in pd.get("options", [])],
            "total_votes":  pd.get("total_vote_count", 0),
            "closed":       pd.get("voting_end_timestamp", 0) < int(time.time() * 1000),
        }

    # Awards (top 5 by coin price)
    awards = []
    for a in sorted(p.get("all_awardings") or [], key=lambda x: -x.get("coin_price", 0)):
        resized = a.get("resized_icons") or []
        icon = clean_url(resized[0]["url"]) if resized else clean_url(a.get("static_icon_url") or a.get("icon_url") or "")
        if icon:
            awards.append({"name": a.get("name", ""), "count": a.get("count", 1), "icon": icon})
        if len(awards) >= 5:
            break

    crosspost_from = None
    if p.get("crosspost_parent_list"):
        orig = p["crosspost_parent_list"][0]
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
    return [process_post(c["data"]) for c in listing["children"] if c.get("kind") == "t3"]


# ── RedGifs proxy ────────────────────────────────────────────────────────────

@app.route("/api/redgifs/<gif_id>")
def get_redgifs(gif_id):
    if not REDGIFS_ID_VALID_RE.match(gif_id):
        return jsonify({"error": "Invalid ID"}), 400
    try:
        token = get_redgifs_token()
        resp  = SESSION.get(
            f"https://api.redgifs.com/v2/gifs/{gif_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "Not found"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"RedGifs returned {resp.status_code}"}), resp.status_code
        urls = resp.json()["gif"]["urls"]
        def proxied(url):
            if not url:
                return None
            fname = url.rsplit("/", 1)[-1]
            return f"/api/redgifs/media/{fname}"
        return cached_json({"hd": proxied(urls.get("hd")), "sd": proxied(urls.get("sd"))}, 3600)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


REDGIFS_MEDIA_RE = re.compile(r'^[A-Za-z0-9_-]+-?(?:mobile|silent)?\.mp4$')

@app.route("/api/redgifs/media/<filename>")
def proxy_redgifs_media(filename):
    if not REDGIFS_MEDIA_RE.match(filename):
        return jsonify({"error": "Invalid filename"}), 400
    url = f"https://media.redgifs.com/{filename}"
    proxy_headers = {
        **HEADERS,
        "Referer":  "https://www.redgifs.com/",
        "Origin":   "https://www.redgifs.com",
        "Accept":   "*/*",
    }
    if "Range" in request.headers:
        proxy_headers["Range"] = request.headers["Range"]
    try:
        upstream = SESSION.get(url, headers=proxy_headers, stream=True, timeout=20)
        resp_headers = {
            "Content-Type":  upstream.headers.get("Content-Type", "video/mp4"),
            "Accept-Ranges": "bytes",
        }
        for h in ("Content-Length", "Content-Range"):
            if h in upstream.headers:
                resp_headers[h] = upstream.headers[h]
        return Response(upstream.iter_content(chunk_size=STREAM_CHUNK_SIZE),
                        status=upstream.status_code, headers=resp_headers)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ── Imgur album proxy ────────────────────────────────────────────────────────

def _imgur_items_to_images(items):
    out = []
    for item in (items or []):
        url = item.get("url") or item.get("link", "")
        if not url:
            continue
        if url.lower().endswith(".gifv"):
            url = url[:-5] + ".mp4"
        out.append({
            "url":         url,
            "width":       item.get("width")  or 0,
            "height":      item.get("height") or 0,
            "description": item.get("description") or "",
        })
    return out


def _imgur_from_next_data(data):
    page_props = data.get("props", {}).get("pageProps", {})
    for obj in (page_props.get("album", {}), page_props.get("ssrData", {}), page_props):
        if not isinstance(obj, dict):
            continue
        for key in ("media", "images", "imgs"):
            items = obj.get(key)
            if isinstance(items, dict):
                items = items.get("images", [])
            imgs = _imgur_items_to_images(items)
            if imgs:
                return imgs
    return None


def _imgur_from_post_data_json(html_text):
    m = re.search(r'window\.postDataJSON\s*=\s*"((?:[^"\\]|\\.)*)"', html_text)
    if not m:
        return None
    try:
        data = json.loads(json.loads('"' + m.group(1) + '"'))
        for key in ("media", "images"):
            imgs = _imgur_items_to_images(data.get(key))
            if imgs:
                return imgs
    except Exception:
        pass
    return None


def _imgur_from_regex(html_text):
    seen, out = set(), []
    for m in IMGUR_IMG_URL_RE.finditer(html_text):
        img_hash, ext = m.group(1), m.group(2).lower()
        base = img_hash[:-1] if (len(img_hash) > 5 and img_hash[-1] in _IMGUR_THUMB_CHARS) else img_hash
        if base not in seen:
            seen.add(base)
            out.append({"url": f"https://i.imgur.com/{base}.{ext}", "width": 0, "height": 0, "description": ""})
    return out or None


def _scrape_imgur_album(album_id):
    resp = SESSION.get(f"https://imgur.com/a/{album_id}", timeout=15)
    resp.raise_for_status()
    html_text = resp.text

    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html_text, re.S)
    if m:
        try:
            imgs = _imgur_from_next_data(json.loads(m.group(1)))
            if imgs:
                return imgs
        except Exception:
            pass

    imgs = _imgur_from_post_data_json(html_text)
    if imgs:
        return imgs

    return _imgur_from_regex(html_text)


@app.route("/api/imgur/album/<album_id>")
def get_imgur_album(album_id):
    if not IMGUR_ALBUM_ID_RE.match(album_id):
        return jsonify({"error": "Invalid album ID"}), 400
    # Official API if client ID is available (legacy support)
    if IMGUR_CLIENT_ID:
        try:
            resp = SESSION.get(
                f"https://api.imgur.com/3/album/{album_id}/images",
                headers={"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"},
                timeout=10)
            if resp.status_code == 200:
                imgs = _imgur_items_to_images(resp.json().get("data", []))
                if imgs:
                    return cached_json({"images": imgs}, CACHE_TTL_SUBREDDIT)
        except Exception:
            pass
    # Fall back to scraping the album page
    try:
        imgs = _scrape_imgur_album(album_id)
        if imgs:
            return cached_json({"images": imgs}, CACHE_TTL_SUBREDDIT)
    except Exception:
        pass
    return jsonify({"error": "no_images"}), 404


# ── Subreddit autocomplete ────────────────────────────────────────────────────

@app.route("/api/subreddit-search")
def subreddit_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"names": []})
    try:
        resp = SESSION.get(
            "https://www.reddit.com/api/search_reddit_names.json",
            params={"query": q, "include_over_18": 0, "exact": 0},
            timeout=5)
        if resp.status_code != 200:
            return jsonify({"names": []})
        return jsonify({"names": resp.json().get("names", [])[:8]})
    except Exception:
        return jsonify({"names": []})


# ── SPA catch-all routes ──────────────────────────────────────────────────────

@app.route("/")
@app.route("/r/<path:path>")
@app.route("/user/<username>")
@app.route("/user/<username>/m/<multiname>")
@app.route("/user/<username>/m/<multiname>/<path:rest>")
@app.route("/u/<username>")
@app.route("/search")
@app.route("/r/<subreddit>/duplicates/<post_id>")
@app.route("/r/<subreddit>/wiki")
@app.route("/r/<subreddit>/wiki/<path:page>")
def spa(**kwargs):
    resp = render_template("index.html")
    return resp, 200, {'Cache-Control': 'no-store'}


# ── Search API ───────────────────────────────────────────────────────────────

SEARCH_SORTS = {'relevance', 'hot', 'top', 'new'}

@app.route("/api/search")
def search_posts():
    q     = request.args.get("q", "").strip()
    sort  = request.args.get("sort", "relevance")
    t     = request.args.get("t", "all")
    after = request.args.get("after", "")
    sub   = request.args.get("sub", "")
    if not q:
        return jsonify({"error": "Missing query"}), 400
    if sort not in SEARCH_SORTS:
        sort = "relevance"
    url    = f"https://www.reddit.com/r/{sub}/search.json" if sub else "https://www.reddit.com/search.json"
    nsfw   = request.args.get("nsfw", "0") == "1"
    params = {"q": q, "sort": sort, "t": t, "limit": FEED_LIMIT, "raw_json": 1, "include_over_18": int(nsfw)}
    if sub:
        params["restrict_sr"] = 1
    if after:
        params["after"] = after
    try:
        resp = SESSION.get(url, params=params, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "Not found"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = extract_posts(listing)
        return cached_json({"posts": posts, "after": listing.get("after")}, CACHE_TTL_FEED)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Subreddit API ─────────────────────────────────────────────────────────────

@app.route("/api/r/<subreddit>")
def get_posts(subreddit):
    sort  = request.args.get("sort", "top")
    t     = request.args.get("t", "")
    after = request.args.get("after", "")
    url   = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    params = {"limit": FEED_LIMIT, "raw_json": 1}
    if sort in ("top", "controversial") and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = SESSION.get(url, params=params, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "Subreddit not found"}), 404
        if resp.status_code == 403:
            return jsonify({"error": "Subreddit is private"}), 403
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = extract_posts(listing)
        return cached_json({"posts": posts, "after": listing.get("after")}, CACHE_TTL_FEED)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/r/<subreddit>/about")
def get_about(subreddit):
    try:
        resp = SESSION.get(
            f"https://www.reddit.com/r/{subreddit}/about.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": "Not found"}), resp.status_code
        d      = resp.json()["data"]
        icon   = clean_url(d.get("icon_img") or d.get("community_icon") or "")
        active = d.get("active_user_count") or d.get("accounts_active") or 0
        return cached_json({
            "title":       d.get("title", subreddit),
            "description": d.get("public_description", ""),
            "sidebar":     d.get("description", ""),
            "subscribers": d.get("subscribers", 0),
            "active":      active,
            "icon":        icon or "",
        }, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/r/<subreddit>/rules")
def get_rules(subreddit):
    try:
        resp = SESSION.get(
            f"https://www.reddit.com/r/{subreddit}/about/rules.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"rules": []})
        rules = resp.json().get("rules", [])
        return cached_json({"rules": [{"short_name": r.get("short_name",""), "description": r.get("description","")} for r in rules]}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        return jsonify({"rules": []})


@app.route("/api/search/communities")
def search_communities():
    q = request.args.get("q", "").strip()
    after = request.args.get("after", "")
    if not q:
        return jsonify({"communities": [], "after": None})
    try:
        params = {"q": q, "limit": FEED_LIMIT, "raw_json": 1, "type": "sr"}
        if after:
            params["after"] = after
        resp = SESSION.get("https://www.reddit.com/search.json",
                           params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"communities": [], "after": None})
        listing = resp.json()["data"]
        results = []
        for c in listing["children"]:
            if c.get("kind") != "t5":
                continue
            d = c["data"]
            icon = clean_url(d.get("icon_img") or d.get("community_icon") or "")
            results.append({
                "name":        d.get("display_name", ""),
                "title":       d.get("title", ""),
                "description": d.get("public_description", ""),
                "subscribers": d.get("subscribers", 0),
                "over_18":     d.get("over_18", False),
                "icon":        icon or "",
            })
        return jsonify({"communities": results, "after": listing.get("after")})
    except Exception as e:
        return jsonify({"communities": [], "after": None})


@app.route("/api/search/users")
def search_users():
    q = request.args.get("q", "").strip()
    after = request.args.get("after", "")
    if not q:
        return jsonify({"users": [], "after": None})
    try:
        params = {"q": q, "limit": FEED_LIMIT, "raw_json": 1, "type": "user"}
        if after:
            params["after"] = after
        resp = SESSION.get("https://www.reddit.com/search.json",
                           params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"users": [], "after": None})
        listing = resp.json()["data"]
        results = []
        for c in listing["children"]:
            if c.get("kind") != "t2":
                continue
            d = c["data"]
            icon = clean_url(d.get("icon_img") or d.get("snoovatar_img") or "")
            results.append({
                "name":          d.get("name", ""),
                "icon":          icon or "",
                "karma_post":    d.get("link_karma", 0),
                "karma_comment": d.get("comment_karma", 0),
                "created_utc":   d.get("created_utc", 0),
            })
        return jsonify({"users": results, "after": listing.get("after")})
    except Exception as e:
        return jsonify({"users": [], "after": None})


@app.route("/api/r/<subreddit>/duplicates/<post_id>")
def get_duplicates(subreddit, post_id):
    try:
        after = request.args.get("after", "")
        params = {"raw_json": 1, "limit": 25}
        if after:
            params["after"] = after
        resp = SESSION.get(
            f"https://old.reddit.com/r/{subreddit}/duplicates/{post_id}.json",
            params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        data = resp.json()
        orig_children = data[0]["data"]["children"]
        post = process_post(orig_children[0]["data"]) if orig_children else None
        if post:
            post["selftext"] = orig_children[0]["data"].get("selftext", "")
        listing = data[1]["data"]
        posts = extract_posts(listing)
        return cached_json({"post": post, "posts": posts, "after": listing.get("after")}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


COMMENT_SORTS = {'confidence', 'top', 'new', 'controversial', 'old', 'qa'}

@app.route("/api/r/<subreddit>/comments/<post_id>")
def get_comments(subreddit, post_id):
    try:
        comment_id = request.args.get('comment')
        sort = request.args.get('sort', 'confidence')
        if sort not in COMMENT_SORTS:
            sort = 'confidence'
        params = {"raw_json": 1, "limit": COMMENTS_LIMIT, "sort": sort}
        if comment_id:
            params["comment"] = comment_id
            params["context"] = 8
        resp = SESSION.get(
            f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json",
            params=params, timeout=12)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        data     = resp.json()
        post_raw = data[0]["data"]["children"][0]["data"]
        post     = process_post(post_raw)
        post["selftext"] = post_raw.get("selftext", "")   # full text in post view

        def parse_comment(c):
            if c["kind"] == "more":
                d = c["data"]
                return {
                    "kind":     "more",
                    "id":       d.get("id", ""),
                    "children": d.get("children", [])[:100],
                    "count":    d.get("count", 0),
                    "depth":    d.get("depth", 0),
                }
            d       = c["data"]
            replies = []
            if d.get("replies") and isinstance(d["replies"], dict):
                for r in d["replies"]["data"]["children"]:
                    parsed = parse_comment(r)
                    if parsed:
                        replies.append(parsed)
            c_edited = d.get("edited")
            c_edited_utc = c_edited if isinstance(c_edited, (int, float)) and c_edited else None
            c_awards = []
            for a in sorted(d.get("all_awardings") or [], key=lambda x: -x.get("coin_price", 0)):
                resized = a.get("resized_icons") or []
                icon = clean_url(resized[0]["url"]) if resized else clean_url(a.get("static_icon_url") or a.get("icon_url") or "")
                if icon:
                    c_awards.append({"name": a.get("name", ""), "count": a.get("count", 1), "icon": icon})
                if len(c_awards) >= 5:
                    break
            return {
                "id":                    d["id"],
                "author":                d.get("author", "[deleted]"),
                "body":                  d.get("body", ""),
                "score":                 d.get("score", 0),
                "created_utc":           d.get("created_utc", 0),
                "edited_utc":            c_edited_utc,
                "depth":                 d.get("depth", 0),
                "replies":               replies,
                "distinguished":         d.get("distinguished"),
                "stickied":              d.get("stickied", False),
                "author_flair_text":     d.get("author_flair_text") or "",
                "author_flair_richtext": d.get("author_flair_richtext") or [],
                "author_flair_type":     d.get("author_flair_type", "text"),
                "author_flair_bg":       d.get("author_flair_background_color") or "",
                "author_flair_tc":       d.get("author_flair_text_color") or "dark",
                "awards":                c_awards,
            }

        comments = [parse_comment(c) for c in data[1]["data"]["children"]]
        return cached_json({"post": post, "comments": [c for c in comments if c]}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/r/<subreddit>/morechildren/<post_id>")
def get_morechildren(subreddit, post_id):
    children = request.args.get("children", "")
    sort     = request.args.get("sort", "confidence")
    if sort not in COMMENT_SORTS:
        sort = "confidence"
    if not children:
        return cached_json({"comments": []}, CACHE_TTL_FEED)
    try:
        resp = SESSION.get(
            "https://www.reddit.com/api/morechildren.json",
            params={"link_id": f"t3_{post_id}", "children": children, "sort": sort,
                    "api_type": "json", "raw_json": 1},
            timeout=12)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        things = resp.json().get("json", {}).get("data", {}).get("things", [])
        by_id  = {}
        ordered = []
        for thing in things:
            if thing["kind"] != "t1":
                continue
            d = thing["data"]
            c_edited = d.get("edited")
            c_edited_utc = c_edited if isinstance(c_edited, (int, float)) and c_edited else None
            c_awards = []
            for a in sorted(d.get("all_awardings") or [], key=lambda x: -x.get("coin_price", 0)):
                resized = a.get("resized_icons") or []
                icon = clean_url(resized[0]["url"]) if resized else clean_url(a.get("static_icon_url") or a.get("icon_url") or "")
                if icon:
                    c_awards.append({"name": a.get("name", ""), "count": a.get("count", 1), "icon": icon})
                if len(c_awards) >= 5:
                    break
            comment = {
                "id":                    d["id"],
                "author":                d.get("author", "[deleted]"),
                "body":                  d.get("body", ""),
                "score":                 d.get("score", 0),
                "created_utc":           d.get("created_utc", 0),
                "edited_utc":            c_edited_utc,
                "depth":                 d.get("depth", 0),
                "replies":               [],
                "distinguished":         d.get("distinguished"),
                "stickied":              d.get("stickied", False),
                "author_flair_text":     d.get("author_flair_text") or "",
                "author_flair_richtext": d.get("author_flair_richtext") or [],
                "author_flair_type":     d.get("author_flair_type", "text"),
                "author_flair_bg":       d.get("author_flair_background_color") or "",
                "author_flair_tc":       d.get("author_flair_text_color") or "dark",
                "awards":                c_awards,
                "_pid":                  d.get("parent_id", ""),
            }
            by_id[d["id"]] = comment
            ordered.append(comment)
        roots = []
        for c in ordered:
            pid = c.pop("_pid", "")
            if pid.startswith("t1_"):
                parent = by_id.get(pid[3:])
                if parent:
                    parent["replies"].append(c)
                    continue
            roots.append(c)
        return cached_json({"comments": roots}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── User API ──────────────────────────────────────────────────────────────────

@app.route("/api/user/<username>/about")
def get_user_about(username):
    try:
        resp = SESSION.get(
            f"https://www.reddit.com/user/{username}/about.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "User not found"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        d    = resp.json()["data"]
        icon = clean_url(d.get("icon_img") or d.get("snoovatar_img") or "")
        return cached_json({
            "name":           d["name"],
            "icon":           icon or "",
            "karma_post":     d.get("link_karma", 0),
            "karma_comment":  d.get("comment_karma", 0),
            "created_utc":    d.get("created_utc", 0),
            "is_premium":     d.get("is_gold", False),
        }, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<username>/posts")
def get_user_posts_api(username):
    sort   = request.args.get("sort", "new")
    t      = request.args.get("t", "")
    after  = request.args.get("after", "")
    params = {"limit": FEED_LIMIT, "raw_json": 1, "sort": sort}
    if sort == "top" and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = SESSION.get(
            f"https://www.reddit.com/user/{username}/submitted.json",
            params=params, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "User not found or profile is private"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = extract_posts(listing)
        return cached_json({"posts": posts, "after": listing.get("after")}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<username>/comments")
def get_user_comments_api(username):
    sort   = request.args.get("sort", "new")
    t      = request.args.get("t", "")
    after  = request.args.get("after", "")
    params = {"limit": FEED_LIMIT, "raw_json": 1, "sort": sort}
    if sort == "top" and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = SESSION.get(
            f"https://www.reddit.com/user/{username}/comments.json",
            params=params, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "User not found or profile is private"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing  = resp.json()["data"]
        comments = []
        for c in listing["children"]:
            if c.get("kind") != "t1":
                continue
            d = c["data"]
            comments.append({
                "id":             d["id"],
                "author":         d.get("author", "[deleted]"),
                "body":           d.get("body", ""),
                "score":          d.get("score", 0),
                "created_utc":    d.get("created_utc", 0),
                "subreddit":      d.get("subreddit", ""),
                "link_title":     d.get("link_title", ""),
                "link_permalink": f"https://www.reddit.com{d.get('link_permalink', '')}",
                "link_id":        d.get("link_id", "").replace("t3_", ""),
            })
        return cached_json({"comments": comments, "after": listing.get("after")}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


WIKI_PAGE_RE = re.compile(r'^[A-Za-z0-9_\-\/\.]+$')

@app.route("/api/r/<subreddit>/wiki")
@app.route("/api/r/<subreddit>/wiki/<path:page>")
def get_wiki(subreddit, page='index'):
    if not WIKI_PAGE_RE.match(page):
        return jsonify({"error": "Invalid page name"}), 400
    try:
        resp = SESSION.get(
            f"https://www.reddit.com/r/{subreddit}/wiki/{page}.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "Wiki page not found"}), 404
        if resp.status_code == 403:
            return jsonify({"error": "Wiki is private or disabled"}), 403
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        d = resp.json()["data"]
        raw_html = html_lib.unescape(d.get("content_html", ""))
        raw_html = re.sub(r'<!--\s*SC_(?:OFF|ON)\s*-->', '', raw_html).strip()
        return cached_json({
            "content_html":   raw_html,
            "revision_date":  d.get("revision_date"),
        }, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<username>/m/<multiname>")
def get_multireddit(username, multiname):
    sort  = request.args.get("sort", "hot")
    t     = request.args.get("t", "")
    after = request.args.get("after", "")
    params = {"limit": FEED_LIMIT, "raw_json": 1}
    if sort in ("top", "controversial") and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        meta = SESSION.get(
            f"https://www.reddit.com/api/multi/user/{username}/m/{multiname}.json",
            params={"raw_json": 1}, timeout=10)
        if meta.status_code == 404:
            return jsonify({"error": "Multireddit not found"}), 404
        if meta.status_code != 200:
            return jsonify({"error": f"Reddit returned {meta.status_code}"}), meta.status_code
        meta_data = meta.json().get("data", {})
        subs = [s["name"] for s in meta_data.get("subreddits", [])]
        if not subs:
            return cached_json({"posts": [], "after": None, "title": multiname}, CACHE_TTL_FEED)
        combined = "+".join(subs[:100])
        resp = SESSION.get(
            f"https://www.reddit.com/r/{combined}/{sort}.json",
            params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        display = meta_data.get("display_name") or meta_data.get("name") or multiname
        return cached_json({"posts": extract_posts(listing), "after": listing.get("after"), "title": display}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, threaded=True)
