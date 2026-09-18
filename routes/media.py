"""RedGifs, Reddit image, and Imgur album proxies, plus the reddit.com redirect resolver."""
import re
import os
import json
import time
import threading
import requests
from urllib.parse import urlparse
from flask import Blueprint, jsonify, request, Response
from reddit_client import SESSION, HEADERS
from helpers import (CACHE_TTL_SUBREDDIT, REDGIFS_TOKEN_TTL, STREAM_CHUNK_SIZE,
                     cached_json, server_cache, log)

bp = Blueprint("media", __name__)


REDGIFS_ID_VALID_RE = re.compile(r'^[a-zA-Z0-9]+$')
IMGUR_ALBUM_ID_RE   = re.compile(r'^[a-zA-Z0-9]+$')
IMGUR_CLIENT_ID     = os.environ.get('IMGUR_CLIENT_ID', '')
IMGUR_IMG_URL_RE    = re.compile(r'https://i\.imgur\.com/([A-Za-z0-9]{5,9})\.(jpe?g|png|gif|webp)', re.I)
_IMGUR_THUMB_CHARS  = frozenset('smbtlr')

_rg_token     = None
_rg_token_exp = 0.0
_rg_lock      = threading.Lock()


def get_redgifs_token():
    global _rg_token, _rg_token_exp
    if _rg_token and time.time() < _rg_token_exp:
        return _rg_token
    with _rg_lock:
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


# ── RedGifs proxy ────────────────────────────────────────────────────────────

def _redgifs_proxied(url):
    if not url: return None
    fname = url.rsplit("/", 1)[-1]
    return f"/api/redgifs/media/{fname}"

@bp.route("/api/redgifs/<gif_id>")
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
        return cached_json({"hd": _redgifs_proxied(urls.get("hd")), "sd": _redgifs_proxied(urls.get("sd"))}, 3600)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/redgifs/batch")
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
            result[gid] = {"hd": _redgifs_proxied(urls.get("hd")), "sd": _redgifs_proxied(urls.get("sd"))}
        return cached_json(result, 3600)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


REDGIFS_MEDIA_RE = re.compile(r'^[A-Za-z0-9_-]+-?(?:mobile|silent)?\.mp4$')

@bp.route("/api/redgifs/media/<filename>")
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
        resp_headers["Cache-Control"] = "public, max-age=604800, immutable"
        return Response(upstream.iter_content(chunk_size=STREAM_CHUNK_SIZE),
                        status=upstream.status_code, headers=resp_headers)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


IMG_PROXY_HOSTS = frozenset({'preview.redd.it', 'external-preview.redd.it'})

@bp.route("/api/img")
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
        resp.headers['Cache-Control'] = 'public, max-age=604800, immutable'
        return resp
    except Exception as e:
        log.warning("proxy_img fetch failed url=%s: %s", url, e)
        return ('', 502)


@bp.route("/api/resolve")
def resolve_url():
    url = request.args.get('url', '').strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return jsonify({'error': 'Invalid URL'}), 400
    hostname = parsed.hostname or ''
    if parsed.scheme not in ('http', 'https') or not (hostname == 'reddit.com' or hostname.endswith('.reddit.com')):
        return jsonify({'error': 'Only reddit.com URLs supported'}), 400
    try:
        r = requests.head(url, allow_redirects=True, timeout=5, headers=HEADERS)
        return jsonify({'url': r.url})
    except Exception:
        log.warning("resolve_url failed url=%s", url)
        return jsonify({'error': 'Request failed'}), 502


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


@bp.route("/api/imgur/album/<album_id>")
@server_cache(CACHE_TTL_SUBREDDIT)
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
