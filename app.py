import os
import re
import json
import html as html_lib
import time
import tempfile
import subprocess
import logging
import requests
from urllib.parse import urlparse, quote as url_quote
from flask import Flask, render_template, jsonify, request, Response, make_response
from flask_compress import Compress
from media_detection import process_post, extract_posts, clean_url, _parse_awards
from reddit_client import reddit_get, SESSION, HEADERS

CACHE_TTL_STATIC     = 604800   # 1 week
CACHE_TTL_FEED       = 300
CACHE_TTL_SUBREDDIT  = 600
REDGIFS_TOKEN_TTL    = 23 * 3600
FEED_LIMIT           = 25
COMMENTS_LIMIT       = 200
STREAM_CHUNK_SIZE    = 65536

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = CACHE_TTL_STATIC
Compress(app)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
REDGIFS_ID_VALID_RE = re.compile(r'^[a-zA-Z0-9]+$')
IMGUR_ALBUM_ID_RE   = re.compile(r'^[a-zA-Z0-9]+$')
IMGUR_CLIENT_ID     = os.environ.get('IMGUR_CLIENT_ID', '')
IMGUR_IMG_URL_RE    = re.compile(r'https://i\.imgur\.com/([A-Za-z0-9]{5,9})\.(jpe?g|png|gif|webp)', re.I)
_IMGUR_THUMB_CHARS  = frozenset('smbtlr')
LIVE_ID_RE          = re.compile(r'^[A-Za-z0-9_-]+$')
OG_IMAGE_RE         = re.compile(r'<meta[^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])[^>]*content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])', re.I)
_og_cache: dict = {}
OG_CACHE_MAX = 1000

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
    log.info("refreshing redgifs token")
    r = SESSION.get("https://api.redgifs.com/v2/auth/temporary", timeout=10)
    if not r.ok:
        log.warning("redgifs token HTTP %s: %s", r.status_code, r.text[:200])
    r.raise_for_status()
    _rg_token     = r.json()["token"]
    _rg_token_exp = time.time() + REDGIFS_TOKEN_TTL
    log.info("redgifs token refreshed, expires in %ss", REDGIFS_TOKEN_TTL)
    return _rg_token


def _parse_comment_fields(d):
    edited = d.get("edited")
    edited_utc = edited if isinstance(edited, (int, float)) and edited else None
    return {
        "id":                    d["id"],
        "author":                d.get("author", "[deleted]"),
        "body":                  d.get("body", ""),
        "score":                 d.get("score", 0),
        "created_utc":           d.get("created_utc", 0),
        "edited_utc":            edited_utc,
        "depth":                 d.get("depth", 0),
        "replies":               [],
        "distinguished":         d.get("distinguished"),
        "stickied":              d.get("stickied", False),
        "author_flair_text":     d.get("author_flair_text") or "",
        "author_flair_richtext": d.get("author_flair_richtext") or [],
        "author_flair_type":     d.get("author_flair_type", "text"),
        "author_flair_bg":       d.get("author_flair_background_color") or "",
        "author_flair_tc":       d.get("author_flair_text_color") or "dark",
        "awards":                _parse_awards(d.get("all_awardings")),
    }


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


@app.route("/api/redgifs/batch")
def get_redgifs_batch():
    raw = request.args.get('ids', '')
    ids = [i for i in raw.split(',') if i and REDGIFS_ID_VALID_RE.match(i)][:50]
    if not ids:
        return jsonify({}), 200
    try:
        token = get_redgifs_token()
        resp = SESSION.get(
            f"https://api.redgifs.com/v2/gifs?ids={','.join(ids)}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"RedGifs returned {resp.status_code}"}), resp.status_code
        gifs = resp.json().get("gifs") or []
        result = {}
        for gif in gifs:
            gid = gif.get("id")
            if not gid:
                continue
            urls = gif.get("urls", {})
            def proxied(url):
                if not url: return None
                fname = url.rsplit("/", 1)[-1]
                return f"/api/redgifs/media/{fname}"
            result[gid] = {"hd": proxied(urls.get("hd")), "sd": proxied(urls.get("sd"))}
        return cached_json(result, 3600)
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


# ── Generic media download proxy ─────────────────────────────────────────────

DOWNLOAD_ALLOWED_HOSTS = frozenset({
    'v.redd.it',
    'i.redd.it',
    'preview.redd.it',
    'external-preview.redd.it',
    'i.imgur.com',
})

IMG_PROXY_HOSTS = frozenset({'preview.redd.it', 'external-preview.redd.it'})

@app.route("/api/img")
def proxy_img():
    url = request.args.get('url', '').strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return ('', 400)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in IMG_PROXY_HOSTS:
        return ('', 403)
    try:
        upstream = SESSION.get(url, headers={'Referer': 'https://www.reddit.com/'}, stream=True, timeout=20)
        if not upstream.ok:
            return ('', upstream.status_code)
        content_type = upstream.headers.get('Content-Type', 'image/jpeg')
        resp = Response(upstream.iter_content(chunk_size=STREAM_CHUNK_SIZE), content_type=content_type)
        resp.headers['Cache-Control'] = 'public, max-age=3600'
        return resp
    except Exception as e:
        log.warning("proxy_img fetch failed url=%s: %s", url, e)
        return ('', 502)


@app.route("/api/resolve")
def resolve_url():
    url = request.args.get('url', '').strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return jsonify({'error': 'Invalid URL'}), 400
    if parsed.scheme not in ('http', 'https') or 'reddit.com' not in parsed.netloc:
        return jsonify({'error': 'Only reddit.com URLs supported'}), 400
    try:
        r = requests.head(url, allow_redirects=True, timeout=5, headers=HEADERS)
        return jsonify({'url': r.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route("/api/download")
def download_media():
    url = request.args.get('url', '').strip()
    filename = re.sub(r'[^\w.\-]', '_', request.args.get('filename', 'media'))[:128]
    try:
        parsed = urlparse(url)
    except Exception:
        return jsonify({'error': 'Invalid URL'}), 400
    if parsed.scheme not in ('http', 'https') or parsed.netloc not in DOWNLOAD_ALLOWED_HOSTS:
        return jsonify({'error': 'URL not allowed'}), 400
    try:
        upstream = SESSION.get(url, stream=True, timeout=30)
        upstream.raise_for_status()
        content_type = upstream.headers.get('Content-Type', 'application/octet-stream')
        resp_headers = {
            'Content-Type': content_type,
            'Content-Disposition': f'attachment; filename="{filename}"',
        }
        if 'Content-Length' in upstream.headers:
            resp_headers['Content-Length'] = upstream.headers['Content-Length']
        return Response(upstream.iter_content(chunk_size=STREAM_CHUNK_SIZE), status=200, headers=resp_headers)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ── Reddit video+audio merge download ────────────────────────────────────────

@app.route("/api/download/reddit-video")
def download_reddit_video():
    hls_url  = request.args.get('hls', '').strip()
    filename = re.sub(r'[^\w.\-]', '_', request.args.get('filename', 'video.mp4'))[:128]

    try:
        parsed = urlparse(hls_url)
    except Exception:
        return jsonify({'error': 'Invalid URL'}), 400
    if parsed.scheme not in ('http', 'https') or parsed.netloc != 'v.redd.it':
        return jsonify({'error': 'URL not allowed'}), 400

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, 'merged.mp4')
            cmd = [
                'ffmpeg', '-y',
                '-user_agent', HEADERS['User-Agent'],
                '-i', hls_url,
                '-c', 'copy',
                '-movflags', '+faststart',
                out_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=180)
            if result.returncode != 0:
                return jsonify({'error': 'ffmpeg failed'}), 502

            with open(out_path, 'rb') as f:
                data = f.read()

        return Response(
            data,
            status=200,
            headers={
                'Content-Type': 'video/mp4',
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(len(data)),
            }
        )
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Processing timed out'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 502


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
    except Exception as e:
        log.debug("_imgur_from_post_data_json parse failed: %s", e)
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
        except Exception as e:
            log.debug("_scrape_imgur_album next-data parse failed: %s", e)

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
        except Exception as e:
            log.warning("imgur API fetch failed album=%s: %s", album_id, e)
    # Fall back to scraping the album page
    try:
        imgs = _scrape_imgur_album(album_id)
        if imgs:
            return cached_json({"images": imgs}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        log.warning("imgur scrape failed album=%s: %s", album_id, e)
    return jsonify({"error": "no_images"}), 404


# ── Subreddit autocomplete ────────────────────────────────────────────────────

@app.route("/api/subreddit-search")
def subreddit_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"names": []})
    try:
        resp = reddit_get(
            "https://www.reddit.com/api/search_reddit_names.json",
            params={"query": q, "include_over_18": 0, "exact": 0},
            timeout=5)
        if resp.status_code != 200:
            return jsonify({"names": []})
        return jsonify({"names": resp.json().get("names", [])[:8]})
    except Exception as e:
        log.warning("subreddit_search failed q=%r: %s", q, e)
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
@app.route("/live/<path:path>")
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
        resp = reddit_get(url, params=params, timeout=10)
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
    f     = request.args.get("f", "").strip()[:200]
    url   = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    params = {"limit": FEED_LIMIT, "raw_json": 1}
    if sort in ("top", "controversial") and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    if f:
        params["f"] = f
    try:
        resp = reddit_get(url, params=params, timeout=10)
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
        resp = reddit_get(
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
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/about/rules.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"rules": []})
        rules = resp.json().get("rules", [])
        return cached_json({"rules": [{"short_name": r.get("short_name",""), "description": r.get("description","")} for r in rules]}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        log.warning("get_rules failed sub=%s: %s", subreddit, e)
        return jsonify({"rules": []})


@app.route("/api/r/<subreddit>/about/moderators")
def get_moderators(subreddit):
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/about/moderators.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"moderators": []})
        children = resp.json().get("data", {}).get("children", [])
        mods = [{"name": m.get("name", "")} for m in children if m.get("name")]
        return cached_json({"moderators": mods}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        log.warning("get_moderators failed sub=%s: %s", subreddit, e)
        return jsonify({"moderators": []})


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
        resp = reddit_get("https://www.reddit.com/search.json",
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
        resp = reddit_get("https://www.reddit.com/search.json",
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
        resp = reddit_get(
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
        resp = reddit_get(
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
            comment = _parse_comment_fields(d)
            comment["replies"] = replies
            return comment

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
        resp = reddit_get(
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
            comment = _parse_comment_fields(d)
            comment["_pid"] = d.get("parent_id", "")
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
        resp = reddit_get(
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
        resp = reddit_get(
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
        resp = reddit_get(
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
                "link_permalink": d.get("link_permalink", "") if d.get("link_permalink", "").startswith("http") else f"https://www.reddit.com{d.get('link_permalink', '')}",
                "link_id":        d.get("link_id", "").replace("t3_", ""),
            })
        return cached_json({"comments": comments, "after": listing.get("after")}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<username>/overview")
def get_user_overview_api(username):
    sort  = request.args.get("sort", "new")
    t     = request.args.get("t", "")
    after = request.args.get("after", "")
    params = {"limit": FEED_LIMIT, "raw_json": 1, "sort": sort}
    if sort == "top" and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = reddit_get(
            f"https://www.reddit.com/user/{username}/overview.json",
            params=params, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "User not found or profile is private"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        items = []
        for child in listing["children"]:
            kind = child.get("kind")
            d    = child.get("data", {})
            if kind == "t3":
                try:
                    items.append({"type": "post", "data": process_post(d)})
                except Exception as e:
                    log.warning("overview process_post failed id=%s: %s", d.get("id"), e)
            elif kind == "t1":
                items.append({"type": "comment", "data": {
                    "id":             d.get("id", ""),
                    "author":         d.get("author", "[deleted]"),
                    "body":           d.get("body", ""),
                    "score":          d.get("score", 0),
                    "created_utc":    d.get("created_utc", 0),
                    "subreddit":      d.get("subreddit", ""),
                    "link_title":     d.get("link_title", ""),
                    "link_permalink": d.get("link_permalink", "") if d.get("link_permalink", "").startswith("http") else f"https://www.reddit.com{d.get('link_permalink', '')}",
                    "link_id":        d.get("link_id", "").replace("t3_", ""),
                }})
        return cached_json({"items": items, "after": listing.get("after")}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


WIKI_PAGE_RE = re.compile(r'^[A-Za-z0-9_\-\/\.]+$')

@app.route("/api/r/<subreddit>/wiki")
@app.route("/api/r/<subreddit>/wiki/<path:page>")
def get_wiki(subreddit, page='index'):
    if not WIKI_PAGE_RE.match(page):
        return jsonify({"error": "Invalid page name"}), 400
    try:
        resp = reddit_get(
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
        meta = reddit_get(
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
        resp = reddit_get(
            f"https://www.reddit.com/r/{combined}/{sort}.json",
            params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        display = meta_data.get("display_name") or meta_data.get("name") or multiname
        return cached_json({"posts": extract_posts(listing), "after": listing.get("after"), "title": display}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Live threads ──────────────────────────────────────────────────────────────

def _parse_live_updates(children):
    out = []
    for c in children:
        if c.get("kind") != "LiveUpdate":
            continue
        d = c["data"]
        out.append({
            "id":          d.get("id", ""),
            "body":        d.get("body", ""),
            "author":      d.get("author", "[deleted]"),
            "created_utc": d.get("created_utc", 0),
            "stricken":    d.get("stricken", False),
        })
    return out


@app.route("/api/live/<thread_id>")
def get_live_thread(thread_id):
    if not LIVE_ID_RE.match(thread_id):
        return jsonify({"error": "Invalid thread ID"}), 400
    try:
        info_resp = reddit_get(
            f"https://www.reddit.com/live/{thread_id}.json",
            params={"raw_json": 1}, timeout=10)
        if info_resp.status_code == 404:
            return jsonify({"error": "Live thread not found"}), 404
        if info_resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {info_resp.status_code}"}), info_resp.status_code
        upd_resp = reddit_get(
            f"https://www.reddit.com/live/{thread_id}/updates.json",
            params={"raw_json": 1, "limit": 25}, timeout=10)
        d = info_resp.json()["data"]
        updates, after = [], None
        if upd_resp.status_code == 200:
            listing = upd_resp.json()["data"]
            updates = _parse_live_updates(listing.get("children", []))
            after   = listing.get("after")
        return cached_json({
            "title":        d.get("title", ""),
            "description":  d.get("description", ""),
            "state":        d.get("state", "complete"),
            "viewer_count": d.get("viewer_count", 0),
            "updates":      updates,
            "after":        after,
        }, 30)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live/<thread_id>/updates")
def get_live_updates(thread_id):
    if not LIVE_ID_RE.match(thread_id):
        return jsonify({"error": "Invalid thread ID"}), 400
    before = request.args.get("before", "")
    after  = request.args.get("after",  "")
    try:
        params = {"raw_json": 1, "limit": 25}
        if before: params["before"] = before
        if after:  params["after"]  = after
        resp = reddit_get(
            f"https://www.reddit.com/live/{thread_id}/updates.json",
            params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        return cached_json({
            "updates": _parse_live_updates(listing.get("children", [])),
            "after":   listing.get("after"),
        }, 15)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/translate")
def translate_text():
    text = request.args.get("text", "").strip()
    if not text:
        return jsonify({"error": "Missing text"}), 400
    try:
        r = SESSION.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:1000], "langpair": "autodetect|en"},
            timeout=8)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        log.warning("translate failed: %s", e)
        return jsonify({"error": str(e)}), 502


@app.route("/api/og-image")
def get_og_image():
    url = request.args.get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"error": "Invalid URL"}), 400
    if url in _og_cache:
        return cached_json({"url": _og_cache[url]}, 3600)
    try:
        r = SESSION.get(url, timeout=8, stream=True, headers={**HEADERS, "Accept": "text/html"})
        # Read only the first 32 KB — enough for <head> tags
        chunk = next(r.iter_content(32768), b"")
        r.close()
        text = chunk.decode("utf-8", errors="ignore")
        m = OG_IMAGE_RE.search(text)
        img_url = (m.group(1) or m.group(2)).strip() if m else None
        if len(_og_cache) >= OG_CACHE_MAX:
            for k in list(_og_cache)[:OG_CACHE_MAX // 5]:
                del _og_cache[k]
        _og_cache[url] = img_url
        return cached_json({"url": img_url}, 3600)
    except Exception as e:
        log.warning("get_og_image failed url=%s: %s", url, e)
        _og_cache[url] = None
        return cached_json({"url": None}, 60)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, threaded=True)
