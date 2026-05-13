import re
import time
import requests
from flask import Flask, render_template, jsonify, request, Response

app = Flask(__name__)
HEADERS    = {"User-Agent": "MinimalRedditViewer/1.0"}
YOUTUBE_RE = re.compile(r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})')
REDGIFS_RE = re.compile(r'redgifs\.com/(?:watch|ifr|embed)/([a-zA-Z0-9]+)|redgifs\.com[^"]*[?&]id=([a-zA-Z0-9]+)', re.I)

_rg_token     = None
_rg_token_exp = 0.0

def get_redgifs_token():
    global _rg_token, _rg_token_exp
    if _rg_token and time.time() < _rg_token_exp:
        return _rg_token
    r = requests.get("https://api.redgifs.com/v2/auth/temporary",
                     headers=HEADERS, timeout=10)
    r.raise_for_status()
    _rg_token     = r.json()["token"]
    _rg_token_exp = time.time() + 23 * 3600
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
        m = re.match(r'(https://v\.redd\.it/[^/?]+)', video_url)
        if m:
            audio_url = m.group(1) + '/DASH_audio.mp4'

    # YouTube
    youtube_id = None
    yt = YOUTUBE_RE.search(p.get("url", ""))
    if yt:
        youtube_id = yt.group(1)

    # Generic iframe embed (non-redgifs, non-reddit, non-youtube)
    embed_url = None
    if not redgifs_id and not is_video and not youtube_id:
        sec       = p.get("secure_media_embed") or {}
        media_url = clean_url(sec.get("media_domain_url", ""))
        if media_url:
            embed_url = media_url

    # Direct GIF / GIFV URLs not already captured as video
    gif_url = None
    gif_is_video = False
    if not is_video and not redgifs_id and not youtube_id and not embed_url:
        post_url  = p.get("url", "")
        lower_url = post_url.lower().split("?")[0]
        if lower_url.endswith(".gif"):
            gif_url = post_url
        elif lower_url.endswith(".gifv"):
            gif_url      = re.sub(r"\.gifv$", ".mp4", post_url, flags=re.I)
            gif_is_video = True

    crosspost_from = None
    if p.get("crosspost_parent_list"):
        orig = p["crosspost_parent_list"][0]
        crosspost_from = {
            "subreddit": orig.get("subreddit", ""),
            "id":        orig.get("id", ""),
            "author":    orig.get("author", "[deleted]"),
        }

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
        "selftext":       p.get("selftext", "")[:600] if p.get("is_self") else "",
        "preview_img":    preview_img,
        "gallery":        gallery,
        "is_video":       is_video,
        "video_url":      video_url,
        "hls_url":        hls_url,
        "audio_url":      audio_url,
        "youtube_id":     youtube_id,
        "embed_url":      embed_url,
        "redgifs_id":     redgifs_id,
        "gif_url":        gif_url,
        "gif_is_video":   gif_is_video,
        "post_hint":      p.get("post_hint", ""),
        "over_18":        p.get("over_18", False),
        "flair":          p.get("link_flair_text") or "",
        "flair_richtext": p.get("link_flair_richtext") or [],
        "flair_type":     p.get("link_flair_type", "text"),
        "domain":         p.get("domain", ""),
        "crosspost_from": crosspost_from,
    }


# ── RedGifs proxy ────────────────────────────────────────────────────────────

@app.route("/api/redgifs/<gif_id>")
def get_redgifs(gif_id):
    if not re.match(r'^[a-zA-Z0-9]+$', gif_id):
        return jsonify({"error": "Invalid ID"}), 400
    try:
        token = get_redgifs_token()
        resp  = requests.get(
            f"https://api.redgifs.com/v2/gifs/{gif_id}",
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
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
        return jsonify({"hd": proxied(urls.get("hd")), "sd": proxied(urls.get("sd"))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


REDGIFS_MEDIA_RE = re.compile(r'^[A-Za-z0-9_-]+-?(?:mobile|silent)?\.mp4$')

@app.route("/api/redgifs/media/<filename>")
def proxy_redgifs_media(filename):
    if not REDGIFS_MEDIA_RE.match(filename):
        return "Invalid filename", 400
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
        upstream = requests.get(url, headers=proxy_headers, stream=True, timeout=20)
        resp_headers = {
            "Content-Type":  upstream.headers.get("Content-Type", "video/mp4"),
            "Accept-Ranges": "bytes",
        }
        for h in ("Content-Length", "Content-Range"):
            if h in upstream.headers:
                resp_headers[h] = upstream.headers[h]
        return Response(upstream.iter_content(chunk_size=65536),
                        status=upstream.status_code, headers=resp_headers)
    except Exception as e:
        return str(e), 502


# ── Subreddit autocomplete ────────────────────────────────────────────────────

@app.route("/api/subreddit-search")
def subreddit_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"names": []})
    try:
        resp = requests.get(
            "https://www.reddit.com/api/search_reddit_names.json",
            headers=HEADERS,
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
@app.route("/u/<username>")
@app.route("/search")
def spa(**kwargs):
    return render_template("index.html")


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
    params = {"q": q, "sort": sort, "t": t, "limit": 25, "raw_json": 1, "include_over_18": int(nsfw)}
    if sub:
        params["restrict_sr"] = 1
    if after:
        params["after"] = after
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "Not found"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = [process_post(c["data"]) for c in listing["children"] if c.get("kind") == "t3"]
        return jsonify({"posts": posts, "after": listing.get("after")})
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
    params = {"limit": 25, "raw_json": 1}
    if sort == "top" and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "Subreddit not found"}), 404
        if resp.status_code == 403:
            return jsonify({"error": "Subreddit is private"}), 403
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = [process_post(c["data"]) for c in listing["children"] if c.get("kind") == "t3"]
        return jsonify({"posts": posts, "after": listing.get("after")})
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/r/<subreddit>/about")
def get_about(subreddit):
    try:
        resp = requests.get(
            f"https://www.reddit.com/r/{subreddit}/about.json",
            headers=HEADERS, params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": "Not found"}), resp.status_code
        d      = resp.json()["data"]
        icon   = clean_url(d.get("icon_img") or d.get("community_icon") or "")
        active = d.get("active_user_count") or d.get("accounts_active") or 0
        return jsonify({
            "title":       d.get("title", subreddit),
            "description": d.get("public_description", ""),
            "subscribers": d.get("subscribers", 0),
            "active":      active,
            "icon":        icon or "",
        })
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
        params = {"raw_json": 1, "limit": 200, "sort": sort}
        if comment_id:
            params["comment"] = comment_id
            params["context"] = 8
        resp = requests.get(
            f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json",
            headers=HEADERS, params=params, timeout=12)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        data     = resp.json()
        post_raw = data[0]["data"]["children"][0]["data"]
        post     = process_post(post_raw)
        post["selftext"] = post_raw.get("selftext", "")   # full text in post view

        def parse_comment(c):
            if c["kind"] == "more":
                return None
            d       = c["data"]
            replies = []
            if d.get("replies") and isinstance(d["replies"], dict):
                for r in d["replies"]["data"]["children"]:
                    parsed = parse_comment(r)
                    if parsed:
                        replies.append(parsed)
            return {
                "id":          d["id"],
                "author":      d.get("author", "[deleted]"),
                "body":        d.get("body", ""),
                "score":       d.get("score", 0),
                "created_utc": d.get("created_utc", 0),
                "depth":       d.get("depth", 0),
                "replies":     replies,
            }

        comments = [parse_comment(c) for c in data[1]["data"]["children"]]
        return jsonify({"post": post, "comments": [c for c in comments if c]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── User API ──────────────────────────────────────────────────────────────────

@app.route("/api/user/<username>/about")
def get_user_about(username):
    try:
        resp = requests.get(
            f"https://www.reddit.com/user/{username}/about.json",
            headers=HEADERS, params={"raw_json": 1}, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "User not found"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        d    = resp.json()["data"]
        icon = clean_url(d.get("icon_img") or d.get("snoovatar_img") or "")
        return jsonify({
            "name":           d["name"],
            "icon":           icon or "",
            "karma_post":     d.get("link_karma", 0),
            "karma_comment":  d.get("comment_karma", 0),
            "created_utc":    d.get("created_utc", 0),
            "is_premium":     d.get("is_gold", False),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<username>/posts")
def get_user_posts_api(username):
    sort   = request.args.get("sort", "new")
    t      = request.args.get("t", "")
    after  = request.args.get("after", "")
    params = {"limit": 25, "raw_json": 1, "sort": sort}
    if sort == "top" and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = requests.get(
            f"https://www.reddit.com/user/{username}/submitted.json",
            headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "User not found or profile is private"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = [process_post(c["data"]) for c in listing["children"] if c.get("kind") == "t3"]
        return jsonify({"posts": posts, "after": listing.get("after")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<username>/comments")
def get_user_comments_api(username):
    sort   = request.args.get("sort", "new")
    t      = request.args.get("t", "")
    after  = request.args.get("after", "")
    params = {"limit": 25, "raw_json": 1, "sort": sort}
    if sort == "top" and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = requests.get(
            f"https://www.reddit.com/user/{username}/comments.json",
            headers=HEADERS, params=params, timeout=10)
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
        return jsonify({"comments": comments, "after": listing.get("after")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, threaded=True)
