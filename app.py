import os
import re
import json
import html as html_lib
import time
import tempfile
import shutil
import subprocess
import logging
import socket
import ipaddress
import uuid
import threading
import requests
from functools import wraps
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, parse_qs, quote as url_quote, unquote as url_unquote
from flask import Flask, render_template, jsonify, request, Response, make_response
from flask_compress import Compress
from bs4 import BeautifulSoup
from media_detection import process_post, extract_posts, clean_url, _parse_awards, extract_redgifs_id, YOUTUBE_RE, STREAMABLE_RE, VREDDDIT_RE, LINK_POST_RE
from reddit_client import reddit_get, SESSION, HEADERS, _get_device
from curl_cffi import requests as cffi_requests

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
logging.getLogger("werkzeug").setLevel(logging.WARNING)
log = logging.getLogger(__name__)
REDGIFS_ID_VALID_RE = re.compile(r'^[a-zA-Z0-9]+$')
_SUB_FEED_RE = re.compile(r'^([A-Za-z0-9_]+)(?:/(hot|new|top|rising|controversial))?$')
_POST_PERMALINK_RE = re.compile(r'^([A-Za-z0-9_]+)/comments/([A-Za-z0-9]+)(?:/[^/]*(?:/([A-Za-z0-9]+))?)?/?$')
SUBREDDIT_RE = re.compile(r'^[A-Za-z0-9_]{1,50}(?:\+[A-Za-z0-9_]{1,50}){0,49}$')
USERNAME_RE  = re.compile(r'^[A-Za-z0-9_-]{1,50}$')
POST_ID_RE   = re.compile(r'^[A-Za-z0-9]{1,10}$')
MULTINAME_RE = re.compile(r'^[A-Za-z0-9_]{1,50}$')
FEED_SORTS   = {'best', 'hot', 'new', 'top', 'rising', 'controversial'}


def hydrate_linked_posts(posts):
    """Batch-fetch full data (title/media) for plain link-posts that point at another
    Reddit post, so they can be rendered with the same embed as real crossposts."""
    targets = [p for p in posts if p.get('linked_post')][:100]
    if not targets:
        return
    ids_str = ','.join(f"t3_{p['linked_post']['id']}" for p in targets)
    try:
        resp = reddit_get('https://www.reddit.com/api/info.json',
                           params={'id': ids_str, 'raw_json': 1}, timeout=8)
        if not resp.ok:
            return
        by_id = {c['data']['id']: c['data'] for c in resp.json()['data']['children']}
        for p in targets:
            raw = by_id.get(p['linked_post']['id'])
            if raw:
                try:
                    p['linked_post'] = process_post(raw)
                except Exception:
                    pass
    except Exception as e:
        log.warning("linked-post batch-fetch failed: %s", e)


def validate_params(**patterns):
    """Route decorator: 400 if a path/view param doesn't match its allowlist regex."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            for key, pattern in patterns.items():
                if key in kwargs and not pattern.match(kwargs[key]):
                    return jsonify({"error": f"Invalid {key}"}), 400
            return f(*args, **kwargs)
        return wrapper
    return decorator
IMGUR_ALBUM_ID_RE   = re.compile(r'^[a-zA-Z0-9]+$')
IMGUR_CLIENT_ID     = os.environ.get('IMGUR_CLIENT_ID', '')
IMGUR_IMG_URL_RE    = re.compile(r'https://i\.imgur\.com/([A-Za-z0-9]{5,9})\.(jpe?g|png|gif|webp)', re.I)
_IMGUR_THUMB_CHARS  = frozenset('smbtlr')
LIVE_ID_RE          = re.compile(r'^[A-Za-z0-9_-]+$')
OG_IMAGE_RE         = re.compile(r'<meta[^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])[^>]*content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])', re.I)
OG_DESC_RE          = re.compile(r'<meta[^>]+(?:property=["\']og:description["\']|name=["\'](?:twitter:description|description)["\'])[^>]*content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property=["\']og:description["\']|name=["\'](?:twitter:description|description)["\'])', re.I)
_og_cache: dict = {}
OG_CACHE_MAX = 1000

_rg_token     = None
_rg_token_exp = 0.0
_rg_lock      = threading.Lock()

def cached_json(data, seconds):
    resp = make_response(jsonify(data))
    resp.headers['Cache-Control'] = f'public, max-age={seconds}'
    return resp

_view_cache: dict = {}
_view_cache_lock = threading.Lock()
VIEW_CACHE_MAX = 1000

def server_cache(ttl):
    """Cache a view's JSON payload in-process for `ttl` seconds, keyed by full
    request path+query, so identical requests (repeat visits, multiple tabs)
    don't re-hit Reddit within the window."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            key = request.full_path
            now = time.time()
            with _view_cache_lock:
                hit = _view_cache.get(key)
            if hit and hit[0] > now:
                return cached_json(hit[1], ttl)
            resp = f(*args, **kwargs)
            cache_control = resp.headers.get('Cache-Control', '') if isinstance(resp, Response) else ''
            if isinstance(resp, Response) and resp.status_code == 200 and 'no-store' not in cache_control and 'private' not in cache_control:
                data = resp.get_json(silent=True)
                if data is not None:
                    with _view_cache_lock:
                        if len(_view_cache) >= VIEW_CACHE_MAX:
                            _view_cache.pop(next(iter(_view_cache)))
                        _view_cache[key] = (now + ttl, data)
            return resp
        return wrapper
    return decorator

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


def _parse_shreddit_crosspost(el):
    """Extract the original post's subreddit/title/media from a crosspost shreddit-post
    element's embedded 'post-media-container' block (the original media is inlined there)."""
    container = el.find('div', {'slot': 'post-media-container'})
    if not container:
        return None

    orig_sub = ''
    orig_id = ''
    orig_title = ''
    credit_bar = container.find(class_='crosspost-credit-bar')
    if credit_bar:
        a = credit_bar.find('a', href=True)
        if a:
            m = re.match(r'^/r/([^/]+)/?$', a['href'])
            if m:
                orig_sub = m.group(1)
    title_div = container.find(class_='crosspost-title')
    if title_div:
        a = title_div.find('a', href=True)
        if a:
            orig_title = a.get_text(strip=True)
            pm = re.match(r'^/r/([^/]+)/comments/([A-Za-z0-9]+)', a['href'])
            if pm:
                orig_sub = orig_sub or pm.group(1)
                orig_id = pm.group(2)

    preview_img = None
    gallery = []
    is_video = False
    video_url = hls_url = audio_url = None

    player = container.find('shreddit-player')
    if player:
        src = player.get('src', '') or ''
        m = VREDDDIT_RE.match(src)
        base = m.group(1) if m else None
        if base:
            is_video = True
            hls_url = base + '/HLSPlaylist.m3u8'
            video_url = base + '/DASH_480.mp4'
            audio_url = base + '/DASH_audio.mp4'
        poster = player.get('poster', '') or ''
        if poster:
            h = urlparse(poster).hostname or ''
            preview_img = (f'/api/img?url={url_quote(poster, safe="")}'
                           if h in ('preview.redd.it', 'external-preview.redd.it') else poster)
    else:
        seen = set()
        for img in container.find_all('img'):
            src = img.get('src', '') or img.get('data-lazy-src', '') or img.get('data-src', '') or ''
            if not src or src in seen:
                continue
            h = urlparse(src).hostname or ''
            if h not in ('preview.redd.it', 'external-preview.redd.it', 'i.redd.it'):
                continue
            seen.add(src)
            proxied = f'/api/img?url={url_quote(src, safe="")}' if h != 'i.redd.it' else src
            if img.has_attr('data-post-media-primary') or not gallery:
                gallery.append({'url': proxied, 'width': 0, 'height': 0, 'caption': ''})
        if len(gallery) == 1:
            preview_img = gallery[0]['url']
            gallery = []
        elif gallery:
            preview_img = gallery[0]['url']

    if not orig_id and not preview_img and not is_video and not gallery:
        return None

    return {
        'id': orig_id, 'title': orig_title, 'author': '[deleted]', 'subreddit': orig_sub,
        'score': 0, 'upvote_ratio': 0, 'num_comments': 0, 'created_utc': 0,
        'url': f'https://www.reddit.com/r/{orig_sub}/comments/{orig_id}' if orig_id else '',
        'permalink': f'https://www.reddit.com/r/{orig_sub}/comments/{orig_id}' if orig_id else '',
        'is_self': False, 'selftext': '', 'selftext_html': None,
        'preview_img': preview_img, 'gallery': gallery,
        'is_video': is_video, 'video_url': video_url, 'hls_url': hls_url, 'audio_url': audio_url,
        'youtube_id': None, 'tiktok_id': None, 'streamable_id': None, 'embed_url': None,
        'redgifs_id': None, 'gif_url': None, 'gif_is_video': False, 'imgur_album_id': None,
        'post_hint': '', 'is_devvit': False, 'devvit_url': None, 'over_18': el.has_attr('is-nsfw'),
        'flair': '', 'flair_richtext': [], 'flair_type': 'text', 'flair_bg': '', 'flair_tc': 'dark',
        'domain': el.get('domain', ''), 'poll': None, 'crosspost_from': None, 'linked_post': None,
        'is_stickied': False, 'is_oc': False, 'is_spoiler': False, 'locked': False,
        'edited_utc': None, 'awards': [],
    }


def _parse_shreddit_post(el):
    raw_id  = el.get('id', '')
    post_id = raw_id[3:] if raw_id.startswith('t3_') else raw_id
    permalink = el.get('permalink', '')
    content_href = el.get('content-href', '') or ''
    post_type = el.get('post-type', '')

    try:
        created_utc = int(datetime.fromisoformat(
            el.get('created-timestamp', '').replace('+0000', '+00:00')
        ).timestamp())
    except Exception:
        created_utc = 0

    try: score = int(el.get('score', 0))
    except Exception: score = 0
    try: upvote_ratio = round(float(el.get('upvote-ratio', 0)) * 100)
    except Exception: upvote_ratio = 0
    try: num_comments = int(el.get('comment-count', 0))
    except Exception: num_comments = 0

    domain_str = el.get('domain', '')
    is_self = post_type in ('self', 'text', 'poll') or domain_str.startswith('self.')
    is_crosspost = post_type == 'crosspost' or el.has_attr('is-crosspost')
    url = content_href if (content_href and not is_self and not is_crosspost) else f'https://www.reddit.com{permalink}'

    selftext = ''
    selftext_html = None
    if is_self:
        body_el = el.find(slot='text-body') or el.find('div', {'slot': 'text-body'})
        if not body_el:
            body_el = el.find('faceplate-html', {'slot': 'text-body'})
        if body_el:
            for a in body_el.find_all('a'):
                a.unwrap()
            inner = body_el.decode_contents().strip()
            if inner:
                selftext_html = inner
                selftext = body_el.get_text(separator=' ').strip()

    preview_img = None
    if post_type == 'image' and content_href:
        h = urlparse(content_href).hostname or ''
        if h in ('preview.redd.it', 'external-preview.redd.it'):
            preview_img = f'/api/img?url={url_quote(content_href, safe="")}'
        else:
            preview_img = content_href

    if not preview_img and post_type == 'link':
        thumb_div = el.find('div', {'slot': 'thumbnail'})
        thumb_img = thumb_div.find('img') if thumb_div else None
        thumb_src = thumb_img.get('src', '') if thumb_img else ''
        if thumb_src:
            h = urlparse(thumb_src).hostname or ''
            preview_img = (f'/api/img?url={url_quote(thumb_src, safe="")}'
                           if h in ('preview.redd.it', 'external-preview.redd.it') else thumb_src)

    is_gallery_url = content_href and '/gallery/' in content_href
    gallery = []
    if post_type == 'gallery' or is_gallery_url:
        seen = set()
        for img in el.find_all(['img', 'faceplate-img']):
            src = (img.get('src', '') or img.get('data-lazy-src', '') or
                   img.get('data-src', '') or '')
            if not src or src in seen:
                continue
            h = urlparse(src).hostname or ''
            if h not in ('preview.redd.it', 'external-preview.redd.it', 'i.redd.it'):
                continue
            seen.add(src)
            proxied = f'/api/img?url={url_quote(src, safe="")}' if h != 'i.redd.it' else src
            fig = img.find_parent('figure')
            cap_el = fig.find('figcaption') if fig else None
            try: w = int(img.get('width', 0) or 0)
            except Exception: w = 0
            try: h_val = int(img.get('height', 0) or 0)
            except Exception: h_val = 0
            gallery.append({'url': proxied, 'width': w, 'height': h_val,
                            'caption': cap_el.get_text().strip() if cap_el else ''})
        if gallery and not preview_img:
            preview_img = gallery[0]['url']

    is_video = post_type in ('video', 'gif')
    video_url = hls_url = audio_url = None
    if is_video and content_href and 'v.redd.it' in content_href:
        m = VREDDDIT_RE.match(content_href)
        base = m.group(1) if m else None
        if base:
            video_url = content_href if '/DASH_' in content_href else base + '/DASH_480.mp4'
            hls_url = base + '/HLSPlaylist.m3u8'
            audio_url = base + '/DASH_audio.mp4'

    redgifs_id = extract_redgifs_id(url)
    yt = YOUTUBE_RE.search(url); youtube_id = yt.group(1) if yt else None
    sm = STREAMABLE_RE.search(url); streamable_id = sm.group(1) if sm else None

    is_devvit = post_type == 'custom'
    devvit_url = (f'https://sh.reddit.com/r/{el.get("subreddit-name", "")}/comments/{post_id}'
                  if is_devvit else None)

    linked_post = None
    if not is_self and not is_crosspost:
        lm = LINK_POST_RE.match(url)
        if lm:
            linked_post = {
                'subreddit': lm.group(1),
                'id':        lm.group(2),
                'title':     lm.group(3).replace('_', ' ').strip() if lm.group(3) else '',
            }

    awards = []
    icon = el.get('award-icon-url', '')
    if icon:
        try: cnt = int(el.get('award-count', 1))
        except Exception: cnt = 1
        awards = [{'name': '', 'count': cnt, 'icon': icon}]

    crosspost_from = _parse_shreddit_crosspost(el) if is_crosspost else None

    return {
        'id': post_id, 'title': el.get('post-title', ''),
        'author': el.get('author', '[deleted]'),
        'subreddit': el.get('subreddit-name', ''),
        'score': score, 'upvote_ratio': upvote_ratio,
        'num_comments': num_comments, 'created_utc': created_utc,
        'url': url, 'permalink': f'https://www.reddit.com{permalink}',
        'is_self': is_self, 'selftext': selftext, 'selftext_html': selftext_html,
        'preview_img': preview_img, 'gallery': gallery,
        'is_video': is_video, 'video_url': video_url,
        'hls_url': hls_url, 'audio_url': audio_url,
        'youtube_id': youtube_id, 'tiktok_id': None,
        'streamable_id': streamable_id, 'embed_url': None,
        'redgifs_id': redgifs_id, 'gif_url': None, 'gif_is_video': False,
        'imgur_album_id': None, 'post_hint': post_type,
        'is_devvit': is_devvit, 'devvit_url': devvit_url,
        'over_18': el.has_attr('is-nsfw'),
        'flair': '', 'flair_richtext': [], 'flair_type': 'text',
        'flair_bg': '', 'flair_tc': 'dark',
        'domain': domain_str, 'poll': None,
        'crosspost_from': crosspost_from, 'linked_post': linked_post, 'is_stickied': False,
        'is_oc': False, 'is_spoiler': el.has_attr('is-spoiler') or el.has_attr('spoiler'), 'locked': False,
        'edited_utc': None, 'awards': awards,
        'recommendation_source': el.get('recommendation-source', ''),
        'feed_label': None,
    }


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

def _redgifs_proxied(url):
    if not url: return None
    fname = url.rsplit("/", 1)[-1]
    return f"/api/redgifs/media/{fname}"

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
        return cached_json({"hd": _redgifs_proxied(urls.get("hd")), "sd": _redgifs_proxied(urls.get("sd"))}, 3600)
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
            result[gid] = {"hd": _redgifs_proxied(urls.get("hd")), "sd": _redgifs_proxied(urls.get("sd"))}
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
        resp_headers["Cache-Control"] = "public, max-age=604800, immutable"
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
    hostname = parsed.hostname or ''
    if parsed.scheme not in ('http', 'https') or not (hostname == 'reddit.com' or hostname.endswith('.reddit.com')):
        return jsonify({'error': 'Only reddit.com URLs supported'}), 400
    try:
        r = requests.head(url, allow_redirects=True, timeout=5, headers=HEADERS)
        return jsonify({'url': r.url})
    except Exception:
        log.warning("resolve_url failed url=%s", url)
        return jsonify({'error': 'Request failed'}), 502

@app.route("/api/download")
def download_media():
    url = request.args.get('url', '').strip()
    filename = re.sub(r'[^\w.\-]', '_', request.args.get('filename', 'media'))[:128]
    filename = re.sub(r'\.{2,}', '.', filename).lstrip('.') or 'media'
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


# ── Gallery zip download ──────────────────────────────────────────────────────

GALLERY_ALLOWED_HOSTS = frozenset({'i.redd.it', 'preview.redd.it', 'external-preview.redd.it'})

@app.route("/api/download/gallery")
def download_gallery():
    import io, zipfile
    from concurrent.futures import ThreadPoolExecutor

    urls_param = request.args.get('urls', '').strip()
    _raw = re.sub(r'[^\w.\-]', '_', request.args.get('name', 'gallery'))
    if len(_raw) > 20:
        _sep = _raw.find('_', 20)
        name = _raw[:_sep] if _sep != -1 else _raw
    else:
        name = _raw
    if not urls_param:
        return jsonify({'error': 'No URLs provided'}), 400
    urls = [u.strip() for u in urls_param.split(',') if u.strip()][:25]
    for url in urls:
        try:
            parsed = urlparse(url)
        except Exception:
            return jsonify({'error': 'Invalid URL'}), 400
        if parsed.scheme not in ('http', 'https') or parsed.netloc not in GALLERY_ALLOWED_HOSTS:
            return jsonify({'error': 'URL not allowed'}), 400

    def _fetch(url):
        try:
            r = SESSION.get(url, stream=True, timeout=20,
                            headers={'Referer': 'https://www.reddit.com/'})
            if not r.ok:
                return None
            path = urlparse(url).path
            ext = path.rsplit('.', 1)[-1].lower() if '.' in path else 'jpg'
            if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'mp4'):
                ext = 'jpg'
            return ext, r.content
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch, urls))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as zf:
        for i, result in enumerate(results, 1):
            if result is None:
                continue
            ext, content = result
            zf.writestr(f'{name}-{i:02d}.{ext}', content)
    buf.seek(0)
    data = buf.read()
    return Response(data, status=200, headers={
        'Content-Type': 'application/zip',
        'Content-Disposition': f'attachment; filename="{name}-gallery.zip"',
        'Content-Length': str(len(data)),
    })


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

    tmpdir = tempfile.mkdtemp()
    out_path = os.path.join(tmpdir, 'merged.mp4')
    try:
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
            shutil.rmtree(tmpdir, ignore_errors=True)
            return jsonify({'error': 'ffmpeg failed'}), 502

        size = os.path.getsize(out_path)

        def _stream():
            try:
                with open(out_path, 'rb') as f:
                    while True:
                        chunk = f.read(STREAM_CHUNK_SIZE)
                        if not chunk:
                            break
                        yield chunk
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        return Response(
            _stream(),
            status=200,
            headers={
                'Content-Type': 'video/mp4',
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(size),
            }
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({'error': 'Processing timed out'}), 504
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
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


# ── Subreddit autocomplete ────────────────────────────────────────────────────

@app.route("/api/subreddit-search")
def subreddit_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"subs": []})
    try:
        resp = reddit_get(
            "https://www.reddit.com/api/subreddit_autocomplete_v2.json",
            params={"query": q, "include_over_18": "true", "include_profiles": "false", "limit": 8},
            timeout=5)
        if resp.status_code != 200:
            return jsonify({"subs": []})
        children = resp.json().get("data", {}).get("children", [])
        subs = [{
            "name":        c["data"].get("display_name", ""),
            "icon":        clean_url(c["data"].get("icon_img") or c["data"].get("community_icon") or ""),
            "subscribers": c["data"].get("subscribers", 0),
            "over18":      bool(c["data"].get("over18")),
        } for c in children if c.get("data", {}).get("display_name")]
        return jsonify({"subs": subs[:8]})
    except Exception as e:
        log.warning("subreddit_search failed q=%r: %s", q, e)
        return jsonify({"subs": []})


_WIDGET_KINDS = {"community-list", "calendar", "image", "textarea", "button", "menu"}


@app.route("/api/r/<subreddit>/widgets")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_widgets(subreddit):
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/api/widgets.json",
            params={"progressive_images": "true", "raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"widgets": []})
        d       = resp.json()
        items   = d.get("items", {})
        order   = (d.get("layout", {}).get("sidebar", {}) or {}).get("order", [])
        topbar  = (d.get("layout", {}).get("topbar", {}) or {}).get("order", [])
        widgets = []
        for wid in [*topbar, *order]:
            w = items.get(wid)
            if not w or w.get("kind") not in _WIDGET_KINDS:
                continue
            kind = w["kind"]
            entry = {"kind": kind, "name": w.get("shortName", "")}
            if kind == "community-list":
                entry["items"] = [{
                    "name": c.get("name", ""),
                    "icon": clean_url(c.get("communityIcon") or c.get("iconUrl") or ""),
                    "subscribers": c.get("subscribers", 0),
                    "over18": bool(c.get("isNSFW")),
                } for c in w.get("data", [])]
            elif kind == "calendar":
                entry["events"] = [{
                    "title": ev.get("title", ""),
                    "startTime": ev.get("startTime"),
                    "allDay": bool(ev.get("allDay")),
                } for ev in w.get("data", [])][:8]
            elif kind == "image":
                entry["images"] = [{
                    "url": clean_url(im.get("url") or ""),
                    "linkUrl": im.get("linkUrl") or "",
                } for im in w.get("data", []) if im.get("url")]
            elif kind == "textarea":
                entry["text"] = w.get("text", "")
            elif kind == "button":
                entry["buttons"] = [{
                    "text": b.get("text", ""),
                    "url": b.get("url", ""),
                    "color": b.get("color", ""),
                } for b in w.get("buttons", [])]
            elif kind == "menu":
                entry["links"] = [{
                    "text": m.get("text", ""),
                    "url": m.get("url", ""),
                } for m in w.get("data", []) if m.get("url")]
            content_key = {"community-list": "items", "calendar": "events", "image": "images",
                           "button": "buttons", "menu": "links"}.get(kind)
            if content_key is not None and not entry.get(content_key):
                continue
            if kind == "textarea" and not entry.get("text", "").strip():
                continue
            widgets.append(entry)
        return cached_json({"widgets": widgets}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        log.warning("get_widgets failed sub=%s: %s", subreddit, e)
        return jsonify({"widgets": []})


# ── SPA catch-all routes ──────────────────────────────────────────────────────

@app.route("/", strict_slashes=False)
@app.route("/home", strict_slashes=False)
@app.route("/home/<sort>", strict_slashes=False)
@app.route("/user/<username>", strict_slashes=False)
@app.route("/user/<username>/m/<multiname>", strict_slashes=False)
@app.route("/user/<username>/m/<multiname>/<path:rest>", strict_slashes=False)
@app.route("/u/<username>", strict_slashes=False)
@app.route("/search", strict_slashes=False)
@app.route("/r/<subreddit>/duplicates/<post_id>", strict_slashes=False)
@app.route("/r/<subreddit>/wiki", strict_slashes=False)
@app.route("/r/<subreddit>/wiki/<path:page>", strict_slashes=False)
@app.route("/live/<path:path>", strict_slashes=False)
def spa(**kwargs):
    initial_profile = None
    username = kwargs.get('username')
    if username and 'multiname' not in kwargs:
        initial_profile = _try_inject_profile(username)
    resp = render_template("index.html", initial_profile=initial_profile)
    return resp, 200, {'Cache-Control': 'no-store'}


def _try_inject_profile(username):
    """Fetch a user's about + overview in parallel for SSR injection."""
    from concurrent.futures import ThreadPoolExecutor

    def _about():
        try:
            data, err = _fetch_user_about(username, timeout=6)
            return None if err else data
        except Exception as e:
            log.warning("inject profile about user=%s: %s", username, e)
            return None

    def _overview():
        try:
            data, err = _fetch_user_overview(username, timeout=6)
            return None if err else data
        except Exception as e:
            log.warning("inject profile overview user=%s: %s", username, e)
            return None

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_about    = ex.submit(_about)
        f_overview = ex.submit(_overview)
        about, overview = f_about.result(), f_overview.result()
    if overview is None:
        return None
    overview["_username"] = username.lower()
    overview["_about"] = about
    return overview


def _try_inject_subreddit(sub, sort, time):
    """Fetch subreddit feed + about in parallel for SSR injection.
    Returns (feed_dict, about_dict); either may be None on error."""
    from concurrent.futures import ThreadPoolExecutor

    def _feed():
        try:
            url = f"https://www.reddit.com/r/{sub}/{sort}.json"
            params = {"limit": FEED_LIMIT, "raw_json": 1}
            if sort in ("top", "controversial") and time in ("hour", "day", "week", "month", "year", "all"):
                params["t"] = time
            r = reddit_get(url, params=params, timeout=6)
            if r.status_code != 200:
                return None
            listing = r.json()["data"]
            return {"posts": extract_posts(listing), "after": listing.get("after"),
                    "_sub": sub.lower(), "_sort": sort, "_time": time}
        except Exception as e:
            log.warning("inject feed sub=%s: %s", sub, e)
            return None

    def _about():
        try:
            r = reddit_get(f"https://www.reddit.com/r/{sub}/about.json",
                           params={"raw_json": 1}, timeout=5)
            if r.status_code != 200:
                return None
            d = r.json()["data"]
            icon = clean_url(d.get("icon_img") or d.get("community_icon") or "")
            active = d.get("active_user_count") or d.get("accounts_active") or 0
            return {"title": d.get("title", sub), "description": d.get("public_description", ""),
                    "subscribers": d.get("subscribers", 0), "active": active,
                    "icon": icon or "", "_sub": sub.lower()}
        except Exception as e:
            log.warning("inject about sub=%s: %s", sub, e)
            return None

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_feed  = ex.submit(_feed)
        f_about = ex.submit(_about)
        return f_feed.result(), f_about.result()


@app.route("/r/<path:reddit_path>")
def r_json_or_spa(reddit_path):
    if reddit_path.endswith(".json"):
        return _proxy_reddit(f"r/{reddit_path}")
    initial_data = initial_about = initial_post = None
    m = _SUB_FEED_RE.match(reddit_path)
    if m:
        sub  = m.group(1)
        sort = m.group(2) or ('hot' if sub.lower() == 'popular' else 'top')
        time = request.args.get('t', 'all')
        initial_data, initial_about = _try_inject_subreddit(sub, sort, time)
    else:
        mp = _POST_PERMALINK_RE.match(reddit_path)
        if mp:
            sub, post_id, comment_id = mp.group(1), mp.group(2), mp.group(3)
            try:
                data, err = _fetch_comments_data(sub, post_id, comment_id, timeout=6)
                if not err:
                    data["_sub"] = sub.lower()
                    data["_post_id"] = post_id
                    data["_comment_id"] = comment_id or ''
                    initial_post = data
            except Exception as e:
                log.warning("inject post sub=%s post=%s: %s", sub, post_id, e)
    resp = render_template("index.html", initial_data=initial_data, initial_about=initial_about, initial_post=initial_post)
    return resp, 200, {'Cache-Control': 'no-store'}


# ── Search API ───────────────────────────────────────────────────────────────

SEARCH_SORTS = {'relevance', 'hot', 'top', 'new'}

@app.route("/api/search")
@server_cache(CACHE_TTL_FEED)
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
        hydrate_linked_posts(posts)
        return cached_json({"posts": posts, "after": listing.get("after")}, CACHE_TTL_FEED)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Subreddit API ─────────────────────────────────────────────────────────────

# Reddit's error responses for non-200 subreddit requests carry a "reason" field
# identifying why access was blocked, distinct from a plain "doesn't exist" 404.
_SUBREDDIT_ERROR_MESSAGES = {
    "banned":      "This subreddit has been banned",
    "private":     "This subreddit is private",
    "quarantined": "This subreddit is quarantined",
    "gated":       "This subreddit requires content-warning acknowledgement",
}

def _subreddit_error_state(resp):
    """Classify a non-200 subreddit response. Returns (state, message)."""
    try:
        body = resp.json() or {}
    except Exception:
        body = {}
    reason = body.get("reason")
    if reason in _SUBREDDIT_ERROR_MESSAGES:
        message = (body.get("quarantine_message") or body.get("interstitial_warning_message")
                   or _SUBREDDIT_ERROR_MESSAGES[reason])
        return reason, message
    if resp.status_code == 404:
        return "not_found", "Subreddit not found"
    if resp.status_code == 403:
        return "private", "Subreddit is private"
    return "error", f"Reddit returned {resp.status_code}"


def _quarantine_fallback_posts(subreddit, after=None, target=FEED_LIMIT):
    """Quarantined subreddit listings are blocked for anonymous sessions even after
    opt-in. Fall back: paginate the comments feed (accessible) to collect post IDs,
    then batch-fetch those posts via /by_id/."""
    from reddit_client import _get_quarantine_session
    s = _get_quarantine_session()
    try:
        seen, ids, cursor = set(), [], after
        for _ in range(4):          # up to 4 pages of comments (400 comments max)
            params = {"limit": 100, "raw_json": 1}
            if cursor:
                params["after"] = cursor
            rc = s.get(
                f"https://www.reddit.com/r/{subreddit}/comments.json",
                params=params,
                timeout=10,
            )
            if not rc.ok:
                break
            data = rc.json().get("data", {})
            for c in data.get("children", []):
                lid = c.get("data", {}).get("link_id", "")
                if lid and lid not in seen:
                    seen.add(lid)
                    ids.append(lid)
            cursor = data.get("after")
            if not cursor or len(ids) >= target:
                break
        if not ids:
            return [], None
        rb = s.get(
            f"https://www.reddit.com/by_id/{','.join(ids[:target])}.json",
            params={"raw_json": 1},
            timeout=10,
        )
        if not rb.ok:
            return [], None
        return extract_posts(rb.json()["data"]), cursor
    except Exception as e:
        return [], None


@app.route("/api/r/<subreddit>")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_FEED)
def get_posts(subreddit):
    sort  = request.args.get("sort", "top")
    if sort not in FEED_SORTS:
        sort = "top"
    t     = request.args.get("t", "")
    after             = request.args.get("after", "")
    quarantine_opt_in = request.args.get("quarantine_opt_in", "")
    url   = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    params = {"limit": FEED_LIMIT, "raw_json": 1}
    if sort in ("top", "controversial") and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = reddit_get(url, quarantine=bool(quarantine_opt_in), params=params, timeout=10)
        if resp.status_code != 200:
            state, message = _subreddit_error_state(resp)
            status = 404 if state in ("banned", "not_found") else 403 if state != "error" else resp.status_code
            return jsonify({"error": message, "state": state}), status
        listing = resp.json()["data"]
        posts   = extract_posts(listing)
        fallback_after = None
        if not posts and quarantine_opt_in:
            posts, fallback_after = _quarantine_fallback_posts(subreddit, after or None)
        hydrate_linked_posts(posts)
        return cached_json({"posts": posts, "after": fallback_after or listing.get("after")}, CACHE_TTL_FEED)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/home")
def get_home():
    sort  = request.args.get("sort", "best")
    t     = request.args.get("t", "")
    after = request.args.get("after", "")
    if sort not in {"best", "hot", "new", "top", "rising", "controversial"}:
        sort = "best"

    cookie = request.headers.get("X-Reddit-Cookie", "").strip()
    if cookie:
        shreddit_sort = {"best": "HOT", "hot": "HOT", "new": "NEW",
                         "top": "TOP", "rising": "RISING",
                         "controversial": "CONTROVERSIAL"}.get(sort, "HOT")
        nav_id = str(uuid.uuid4())
        params = {"sort": shreddit_sort, "distance": 4, "adDistance": 2,
                  "navigationSessionId": nav_id, "referer": "www.reddit.com"}
        if sort in ("top", "controversial") and t in ("hour", "day", "week", "month", "year", "all"):
            params["t"] = t
        if after:
            params["after"] = after
            params["cursor"] = after
        try:
            resp = cffi_requests.get(
                "https://www.reddit.com/svc/shreddit/feeds/home-feed",
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:151.0) Gecko/20100101 Firefox/151.0",
                    "Accept": "text/vnd.reddit.partial+html, text/html;q=0.9",
                    "Cookie": cookie,
                    "x-reddit-client-version": "2026-06-04T20:11Z~59b9f87c",
                    "Referer": "https://www.reddit.com/?feed=home",
                    "x-original-referer": "https://www.reddit.com/?feed=home",
                },
                impersonate="firefox133",
                timeout=15,
            )
            if resp.ok:
                soup = BeautifulSoup(resp.text, 'html.parser')
                posts = []
                current_label = None
                last_source = None
                _SKIP_TAGS = {'script', 'hr', 'faceplate-loader', 'faceplate-partial',
                              'ac-publish', 'style', 'link', 'meta',
                              'shreddit-ad-post'}
                for child in soup.children:
                    if not hasattr(child, 'name') or not child.name:
                        continue
                    if child.name == 'article':
                        post_el = child.find('shreddit-post')
                        if post_el:
                            if (post_el.has_attr('promoted') or
                                    child.find('shreddit-ad-post') or
                                    'promoted' in str(child.attrs).lower()):
                                continue
                            post = _parse_shreddit_post(post_el)
                            src = post.get('recommendation_source', '')
                            if current_label and src != last_source:
                                post['feed_label'] = current_label
                                current_label = None
                            last_source = src
                            posts.append(post)
                        elif child.find('shreddit-ad-post') or 'promotedlink' in ' '.join(child.get('class') or []):
                            continue
                    elif child.name not in _SKIP_TAGS:
                        txt = child.get_text(separator=' ', strip=True)
                        if txt and len(txt) < 300:
                            current_label = txt
                # Batch-fetch gallery data for gallery posts where HTML parsing found no images
                missing = [(i, p['id']) for i, p in enumerate(posts)
                           if p['post_hint'] == 'gallery' and not p['gallery']]
                if missing:
                    ids_str = ','.join(f't3_{pid}' for _, pid in missing)
                    try:
                        gi = reddit_get('https://www.reddit.com/api/info.json',
                                        params={'id': ids_str, 'raw_json': 1}, timeout=8)
                        if gi.ok:
                            by_id = {c['data']['id']: c['data']
                                     for c in gi.json()['data']['children']}
                            for i, pid in missing:
                                if pid in by_id:
                                    full = process_post(by_id[pid])
                                    if full.get('gallery'):
                                        posts[i]['gallery'] = full['gallery']
                                        if full.get('preview_img'):
                                            posts[i]['preview_img'] = full['preview_img']
                    except Exception as ge:
                        log.warning("gallery batch-fetch failed: %s", ge)
                hydrate_linked_posts(posts)
                next_after = None
                next_partial = soup.find('faceplate-partial', id='feed-next-page-partial')
                if next_partial and next_partial.get('src'):
                    qs = parse_qs(urlparse(next_partial['src']).query)
                    next_after = (qs.get('after') or qs.get('cursor') or [None])[0]
                if not next_after:
                    m = re.search(r'"after"\s*:\s*"([A-Za-z0-9_-]+)"', resp.text)
                    next_after = m.group(1) if m else None
                resp_out = make_response(jsonify({"posts": posts, "after": next_after, "via": "shreddit"}))
                resp_out.headers['Cache-Control'] = 'private, no-store'
                return resp_out
            else:
                log.warning("shreddit home-feed non-OK: %s %.300s", resp.status_code, resp.text)
        except Exception as e:
            log.warning("shreddit home-feed failed: %s", e)

    # Fallback: anonymous JSON API
    url    = f"https://www.reddit.com/{sort}.json"
    params = {"limit": FEED_LIMIT, "raw_json": 1}
    if sort in ("top", "controversial") and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = reddit_get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = extract_posts(listing)
        hydrate_linked_posts(posts)
        return cached_json({"posts": posts, "after": listing.get("after"), "via": "anonymous"}, CACHE_TTL_FEED)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/r/<subreddit>/about")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_FEED)
def get_about(subreddit):
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/about.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            state, message = _subreddit_error_state(resp)
            status = 404 if state in ("banned", "not_found") else 403 if state != "error" else resp.status_code
            return jsonify({"error": message, "state": state}), status
        d      = resp.json()["data"]
        icon   = clean_url(d.get("icon_img") or d.get("community_icon") or "")
        active = d.get("active_user_count") or d.get("accounts_active") or 0
        sub_type = d.get("subreddit_type", "public")
        state = "quarantined" if d.get("quarantine") else (sub_type if sub_type != "public" else None)
        return cached_json({
            "title":       d.get("title", subreddit),
            "description": d.get("public_description", ""),
            "sidebar":     d.get("description", ""),
            "subscribers": d.get("subscribers", 0),
            "active":      active,
            "icon":        icon or "",
            "state":       state,
        }, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/r/<subreddit>/rules")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
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
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
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
@server_cache(CACHE_TTL_FEED)
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
@server_cache(CACHE_TTL_FEED)
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
@validate_params(subreddit=SUBREDDIT_RE, post_id=POST_ID_RE)
@server_cache(CACHE_TTL_FEED)
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
        hydrate_linked_posts(([post] if post else []) + posts)
        return cached_json({"post": post, "posts": posts, "after": listing.get("after")}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


COMMENT_SORTS = {'confidence', 'top', 'new', 'controversial', 'old', 'qa'}

def _fetch_comments_data(subreddit, post_id, comment_id=None, sort='confidence', timeout=12):
    """Fetch + parse a post's comments. Returns (data_dict, None) or (None, (error_msg, status))."""
    if sort not in COMMENT_SORTS:
        sort = 'confidence'
    params = {"raw_json": 1, "limit": COMMENTS_LIMIT, "sort": sort}
    if comment_id:
        params["comment"] = comment_id
        params["context"] = 8
    resp = reddit_get(
        f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json",
        params=params, timeout=timeout)
    if resp.status_code == 403:
        state, _ = _subreddit_error_state(resp)
        if state == "quarantined":
            resp = reddit_get(
                f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json",
                quarantine=True, params=params, timeout=timeout)
    if resp.status_code != 200:
        return None, (f"Reddit returned {resp.status_code}", resp.status_code)
    data     = resp.json()
    children = data[0]["data"]["children"]
    if not children:
        return None, ("Post not found", 404)
    post_raw = children[0]["data"]
    post     = process_post(post_raw)
    post["selftext"] = post_raw.get("selftext", "")   # full text in post view
    hydrate_linked_posts([post])

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
    return {"post": post, "comments": [c for c in comments if c]}, None


@app.route("/api/r/<subreddit>/comments/<post_id>")
@validate_params(subreddit=SUBREDDIT_RE, post_id=POST_ID_RE)
@server_cache(CACHE_TTL_FEED)
def get_comments(subreddit, post_id):
    try:
        comment_id = request.args.get('comment')
        sort = request.args.get('sort', 'confidence')
        data, err = _fetch_comments_data(subreddit, post_id, comment_id, sort)
        if err:
            msg, status = err
            return jsonify({"error": msg}), status
        return cached_json(data, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/r/<subreddit>/morechildren/<post_id>")
@validate_params(subreddit=SUBREDDIT_RE, post_id=POST_ID_RE)
@server_cache(CACHE_TTL_FEED)
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

ARCTIC_SHIFT_BASE = "https://arctic-shift.photon-reddit.com/api"


def arctic_shift_get(path, params, timeout=10):
    return SESSION.get(f"{ARCTIC_SHIFT_BASE}{path}", params=params, timeout=timeout)


def _normalize_comment(d):
    link_permalink = d.get("link_permalink", "") or d.get("permalink", "") or ""
    if link_permalink and not link_permalink.startswith("http"):
        link_permalink = f"https://www.reddit.com{link_permalink}"
    return {
        "id":             d.get("id", ""),
        "author":         d.get("author", "[deleted]"),
        "body":           d.get("body", ""),
        "score":          d.get("score", 0),
        "created_utc":    d.get("created_utc", 0),
        "subreddit":      d.get("subreddit", ""),
        "link_title":     d.get("link_title", ""),
        "link_permalink": link_permalink,
        "link_id":        (d.get("link_id") or "").replace("t3_", ""),
    }


def _backfill_comment_titles(comments):
    """Arctic Shift comments lack link_title/link_permalink; batch-fetch parent posts."""
    link_ids = sorted({c["link_id"] for c in comments if c["link_id"] and not c["link_title"]})
    if not link_ids:
        return
    try:
        resp = arctic_shift_get("/posts/ids", {"ids": ",".join(link_ids[:500])})
        resp.raise_for_status()
        posts = {p["id"]: p for p in resp.json().get("data", [])}
        for c in comments:
            p = posts.get(c["link_id"])
            if p:
                if not c["link_title"]:
                    c["link_title"] = p.get("title", "")
                if not c["link_permalink"]:
                    permalink = p.get("permalink", "")
                    c["link_permalink"] = f"https://www.reddit.com{permalink}" if permalink else ""
    except Exception as e:
        log.warning("archived comment link_title backfill failed: %s", e)


def _fetch_archived_posts(username, limit, before=None):
    params = {"author": username, "sort": "desc", "limit": limit}
    if before:
        params["before"] = before
    resp = arctic_shift_get("/posts/search", params)
    resp.raise_for_status()
    posts = []
    for d in resp.json().get("data", []):
        try:
            posts.append(process_post(d))
        except Exception as e:
            log.warning("archived process_post failed id=%s: %s", d.get("id"), e)
    return posts


def _fetch_archived_comments(username, limit, before=None):
    params = {"author": username, "sort": "desc", "limit": limit}
    if before:
        params["before"] = before
    resp = arctic_shift_get("/comments/search", params)
    resp.raise_for_status()
    comments = [_normalize_comment(d) for d in resp.json().get("data", [])]
    _backfill_comment_titles(comments)
    return comments


def _arc_cursor(items, limit):
    """Next-page cursor for archived (Arctic Shift) listings: 'arc:<created_utc>'
    of the oldest item, only offered when the page was full (may be more)."""
    if len(items) < limit:
        return None
    return f"arc:{items[-1]['created_utc']}"


@app.route("/api/posts/live-info")
@server_cache(30)
def get_posts_live_info():
    """Arctic Shift's score/comment-count is a snapshot from whenever it first
    crawled the post, which for recently-posted content is often just the
    author's initial upvote. Lets the frontend lazily refresh archived post
    cards with current numbers from Reddit, without blocking the initial
    (already-slow) archived-feed response on it."""
    ids = [i for i in request.args.get("ids", "").split(",") if POST_ID_RE.match(i)][:100]
    if not ids:
        return jsonify({})
    try:
        resp = reddit_get(
            "https://www.reddit.com/api/info.json",
            params={"id": ",".join(f"t3_{i}" for i in ids), "raw_json": 1},
            timeout=8)
        if not resp.ok:
            return jsonify({})
        out = {}
        for c in resp.json().get("data", {}).get("children", []):
            d = c["data"]
            out[d["id"]] = {"score": d.get("score", 0), "num_comments": d.get("num_comments", 0)}
        return cached_json(out, 30)
    except Exception as e:
        log.warning("live-info fetch failed: %s", e)
        return jsonify({})


def _fetch_user_about(username, timeout=10):
    """Returns (data_dict, None) or (None, (error_msg, status))."""
    resp = reddit_get(
        f"https://www.reddit.com/user/{username}/about.json",
        params={"raw_json": 1}, timeout=timeout)
    if resp.status_code == 404:
        return None, ("User not found", 404)
    if resp.status_code != 200:
        return None, (f"Reddit returned {resp.status_code}", resp.status_code)
    d    = resp.json()["data"]
    icon = clean_url(d.get("icon_img") or d.get("snoovatar_img") or "")
    sub  = d.get("subreddit") or {}
    return {
        "name":                d["name"],
        "icon":                icon or "",
        "description":         sub.get("public_description", "") or "",
        "karma_post":          d.get("link_karma", 0),
        "karma_comment":       d.get("comment_karma", 0),
        "karma_award":         d.get("awarder_karma", 0),
        "karma_total":         d.get("total_karma", d.get("link_karma", 0) + d.get("comment_karma", 0)),
        "created_utc":         d.get("created_utc", 0),
        "is_premium":          d.get("is_gold", False),
        "is_mod":              d.get("is_mod", False),
        "is_employee":         d.get("is_employee", False),
        "verified":            d.get("verified", False),
        "has_verified_email":  d.get("has_verified_email", False),
    }, None


@app.route("/api/user/<username>/about")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_user_about(username):
    try:
        data, err = _fetch_user_about(username)
        if err:
            msg, status = err
            return jsonify({"error": msg}), status
        return cached_json(data, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<username>/trophies")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_user_trophies(username):
    try:
        resp = reddit_get(
            f"https://www.reddit.com/api/v1/user/{username}/trophies.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"trophies": []})
        trophies = resp.json().get("data", {}).get("trophies", [])
        out = []
        for t in trophies:
            td = t.get("data", {})
            if not td.get("name"):
                continue
            out.append({
                "name":        td.get("name", ""),
                "description": td.get("description") or "",
                "icon":        clean_url(td.get("icon_40") or td.get("icon_70") or ""),
                "granted_at":  td.get("granted_at") or 0,
            })
        return cached_json({"trophies": out}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        log.warning("get_user_trophies failed user=%s: %s", username, e)
        return jsonify({"trophies": []})


@app.route("/api/user/<username>/posts")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_user_posts_api(username):
    sort   = request.args.get("sort", "new")
    t      = request.args.get("t", "")
    after  = request.args.get("after", "")

    if after.startswith("arc:"):
        try:
            posts = _fetch_archived_posts(username, FEED_LIMIT, before=int(after[4:]))
            hydrate_linked_posts(posts)
            return cached_json({"posts": posts, "after": _arc_cursor(posts, FEED_LIMIT), "archived": True}, CACHE_TTL_FEED)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    params = {"limit": FEED_LIMIT, "raw_json": 1, "sort": sort}
    if sort == "top" and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = reddit_get(
            f"https://www.reddit.com/user/{username}/submitted.json",
            params=params, timeout=10)
        if resp.status_code in (403, 404):
            try:
                posts = _fetch_archived_posts(username, FEED_LIMIT)
                if posts:
                    hydrate_linked_posts(posts)
                    return cached_json({"posts": posts, "after": _arc_cursor(posts, FEED_LIMIT), "archived": True}, CACHE_TTL_FEED)
            except Exception as e:
                log.warning("archived posts fallback failed for %s: %s", username, e)
            return jsonify({"error": "User not found or profile is private"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = extract_posts(listing)
        if not posts and not after:
            try:
                archived = _fetch_archived_posts(username, FEED_LIMIT)
                if archived:
                    hydrate_linked_posts(archived)
                    return cached_json({"posts": archived, "after": _arc_cursor(archived, FEED_LIMIT), "archived": True}, CACHE_TTL_FEED)
            except Exception as e:
                log.warning("archived posts fallback failed for %s: %s", username, e)
        hydrate_linked_posts(posts)
        return cached_json({"posts": posts, "after": listing.get("after")}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<username>/comments")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_user_comments_api(username):
    sort   = request.args.get("sort", "new")
    t      = request.args.get("t", "")
    after  = request.args.get("after", "")

    if after.startswith("arc:"):
        try:
            comments = _fetch_archived_comments(username, FEED_LIMIT, before=int(after[4:]))
            return cached_json({"comments": comments, "after": _arc_cursor(comments, FEED_LIMIT), "archived": True}, CACHE_TTL_FEED)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    params = {"limit": FEED_LIMIT, "raw_json": 1, "sort": sort}
    if sort == "top" and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    try:
        resp = reddit_get(
            f"https://www.reddit.com/user/{username}/comments.json",
            params=params, timeout=10)
        if resp.status_code in (403, 404):
            try:
                comments = _fetch_archived_comments(username, FEED_LIMIT)
                if comments:
                    return cached_json({"comments": comments, "after": _arc_cursor(comments, FEED_LIMIT), "archived": True}, CACHE_TTL_FEED)
            except Exception as e:
                log.warning("archived comments fallback failed for %s: %s", username, e)
            return jsonify({"error": "User not found or profile is private"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing  = resp.json()["data"]
        comments = []
        for c in listing["children"]:
            if c.get("kind") != "t1":
                continue
            comments.append(_normalize_comment(c["data"]))
        if not comments and not after:
            try:
                archived = _fetch_archived_comments(username, FEED_LIMIT)
                if archived:
                    return cached_json({"comments": archived, "after": _arc_cursor(archived, FEED_LIMIT), "archived": True}, CACHE_TTL_FEED)
            except Exception as e:
                log.warning("archived comments fallback failed for %s: %s", username, e)
        return cached_json({"comments": comments, "after": listing.get("after")}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _fetch_archived_overview(username, before=None):
    posts    = _fetch_archived_posts(username, FEED_LIMIT, before=before)
    comments = _fetch_archived_comments(username, FEED_LIMIT, before=before)
    hydrate_linked_posts(posts)
    items = (
        [{"type": "post", "data": p} for p in posts]
        + [{"type": "comment", "data": c} for c in comments]
    )
    items.sort(key=lambda i: i["data"].get("created_utc", 0), reverse=True)
    next_after = None
    if len(posts) == FEED_LIMIT or len(comments) == FEED_LIMIT:
        next_after = f"arc:{items[-1]['data']['created_utc']}" if items else None
    return items, next_after


def _fetch_user_overview(username, sort='new', t='', after='', timeout=10):
    """Returns (data_dict, None) or (None, (error_msg, status))."""
    if after.startswith("arc:"):
        items, next_after = _fetch_archived_overview(username, before=int(after[4:]))
        return {"items": items, "after": next_after, "archived": True}, None

    params = {"limit": FEED_LIMIT, "raw_json": 1, "sort": sort}
    if sort == "top" and t in ("hour", "day", "week", "month", "year", "all"):
        params["t"] = t
    if after:
        params["after"] = after
    resp = reddit_get(
        f"https://www.reddit.com/user/{username}/overview.json",
        params=params, timeout=timeout)
    if resp.status_code in (403, 404):
        try:
            items, next_after = _fetch_archived_overview(username)
            if items:
                return {"items": items, "after": next_after, "archived": True}, None
        except Exception as e:
            log.warning("archived overview fallback failed for %s: %s", username, e)
        return None, ("User not found or profile is private", 404)
    if resp.status_code != 200:
        return None, (f"Reddit returned {resp.status_code}", resp.status_code)
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
            items.append({"type": "comment", "data": _normalize_comment(d)})
    hydrate_linked_posts([i["data"] for i in items if i["type"] == "post"])
    if not items and not after:
        try:
            arc_items, next_after = _fetch_archived_overview(username)
            if arc_items:
                return {"items": arc_items, "after": next_after, "archived": True}, None
        except Exception as e:
            log.warning("archived overview fallback failed for %s: %s", username, e)
    return {"items": items, "after": listing.get("after")}, None


@app.route("/api/user/<username>/overview")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_user_overview_api(username):
    sort  = request.args.get("sort", "new")
    t     = request.args.get("t", "")
    after = request.args.get("after", "")
    try:
        data, err = _fetch_user_overview(username, sort, t, after)
        if err:
            msg, status = err
            return jsonify({"error": msg}), status
        return cached_json(data, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


WIKI_PAGE_RE = re.compile(r'^[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*$')

@app.route("/api/r/<subreddit>/wiki")
@app.route("/api/r/<subreddit>/wiki/<path:page>")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
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
@validate_params(username=USERNAME_RE, multiname=MULTINAME_RE)
@server_cache(CACHE_TTL_FEED)
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
        posts   = extract_posts(listing)
        hydrate_linked_posts(posts)
        return cached_json({"posts": posts, "after": listing.get("after"), "title": display}, CACHE_TTL_FEED)
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
        from concurrent.futures import ThreadPoolExecutor
        def _fetch_info():
            return reddit_get(f"https://www.reddit.com/live/{thread_id}.json", params={"raw_json": 1}, timeout=10)
        def _fetch_updates():
            return reddit_get(f"https://www.reddit.com/live/{thread_id}/updates.json", params={"raw_json": 1, "limit": 25}, timeout=10)
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_info = ex.submit(_fetch_info)
            f_upd  = ex.submit(_fetch_updates)
            info_resp = f_info.result()
            upd_resp  = f_upd.result()
        if info_resp.status_code == 404:
            return jsonify({"error": "Live thread not found"}), 404
        if info_resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {info_resp.status_code}"}), info_resp.status_code
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


_PRIVATE_NETS = [
    ipaddress.ip_network(cidr) for cidr in (
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
    )
]

def _resolve_ssrf_safe(hostname: str):
    """Resolve hostname to IP and verify it's not private. Returns IP string or None."""
    try:
        resolved = socket.gethostbyname(hostname)
        addr = ipaddress.ip_address(resolved)
        if any(addr in net for net in _PRIVATE_NETS):
            return None
        return resolved
    except Exception:
        return None


@app.route("/api/og-image")
def get_og_image():
    url = request.args.get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"error": "Invalid URL"}), 400
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
    except Exception:
        return jsonify({"error": "Invalid URL"}), 400
    if not hostname:
        return jsonify({"error": "Invalid URL"}), 400
    resolved_ip = _resolve_ssrf_safe(hostname)
    if not resolved_ip:
        return jsonify({"error": "URL not allowed"}), 403
    if url in _og_cache:
        return cached_json(_og_cache[url], 3600)
    # For HTTP, connect directly to the resolved IP to prevent DNS rebinding TOCTOU.
    # For HTTPS, SSL certificate validation prevents rebinding (cert won't match a spoofed IP).
    if parsed.scheme == "http":
        safe_netloc = parsed.netloc.replace(hostname, resolved_ip, 1)
        fetch_url = urlunparse(parsed._replace(netloc=safe_netloc))
        fetch_headers = {**HEADERS, "Accept": "text/html", "Host": parsed.netloc}
    else:
        fetch_url = url
        fetch_headers = {**HEADERS, "Accept": "text/html"}
    try:
        r = SESSION.get(fetch_url, timeout=8, stream=True, headers=fetch_headers)
        # Read only the first 32 KB — enough for <head> tags
        chunk = next(r.iter_content(32768), b"")
        r.close()
        text = chunk.decode("utf-8", errors="ignore")
        m = OG_IMAGE_RE.search(text)
        img_url = (m.group(1) or m.group(2)).strip() if m else None
        d = OG_DESC_RE.search(text)
        desc = html_lib.unescape(d.group(1) or d.group(2)).strip() if d else None
        result = {"url": img_url, "description": desc or None}
        if len(_og_cache) >= OG_CACHE_MAX:
            for k in list(_og_cache)[:OG_CACHE_MAX // 5]:
                del _og_cache[k]
        _og_cache[url] = result
        return cached_json(result, 3600)
    except Exception as e:
        log.warning("get_og_image failed url=%s: %s", url, e)
        result = {"url": None, "description": None}
        _og_cache[url] = result
        return cached_json(result, 60)


_DEVVIT_URL_RE = re.compile(r'^https://www\.reddit\.com/r/[^/]+/comments/[^/]+/[^/]+/?$')
_devvit_cache: dict = {}
DEVVIT_CACHE_MAX = 200

@app.route("/api/devvit")
def get_devvit_embed():
    """Fetch the Devvit webview entrypoint URL for a custom post."""
    permalink = request.args.get('url', '').strip()
    if not permalink or not _DEVVIT_URL_RE.match(permalink):
        return jsonify({'error': 'Invalid URL'}), 400
    if permalink in _devvit_cache:
        return cached_json(_devvit_cache[permalink], 3600)
    try:
        device = _get_device()
        hdrs = {**device.api_headers(), 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'}
        r = cffi_requests.get(permalink, impersonate=device.impersonate,
                              headers=hdrs, timeout=20, allow_redirects=True)
        m = re.search(r'<devvit2-surface[^>]+\binit="([^"]+)"', r.text, re.I)
        if not m:
            result = {'embedded': False}
        else:
            init = json.loads(html_lib.unescape(m.group(1)))
            entry = init.get('entrypointUrl', '')
            if not entry or 'devvit.net' not in entry:
                result = {'embedded': False}
            else:
                height = (init.get('postStyles') or {}).get('heightPixels', 512)
                result = {'embedded': True, 'url': entry, 'height': int(height)}
        if len(_devvit_cache) >= DEVVIT_CACHE_MAX:
            _devvit_cache.pop(next(iter(_devvit_cache)))
        _devvit_cache[permalink] = result
        return cached_json(result, 3600)
    except Exception as e:
        log.error('devvit embed error url=%s: %s', permalink, e)
        return jsonify({'embedded': False}), 200


def _proxy_reddit(reddit_path):
    url = f"https://oauth.reddit.com/{reddit_path}"
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8")
    try:
        resp = reddit_get(url, timeout=15)
    except Exception as e:
        log.warning("proxy request failed url=%s: %s", url, e)
        return jsonify({"error": "upstream request failed"}), 502
    content_type = resp.headers.get("Content-Type", "application/json")
    return Response(resp.content, status=resp.status_code, content_type=content_type)



@app.route("/api/platform")
def get_platform():
    return jsonify({"android": bool(os.environ.get("RDVWR_UPDATE_DIR"))})


@app.route("/api/update", methods=["POST"])
def do_update():
    import io, tarfile, hashlib
    update_dir = os.environ.get("RDVWR_UPDATE_DIR", "")
    if not update_dir:
        return jsonify({"status": "unsupported"})
    REPO = "evanpaul14/rdvwr-android"
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{REPO}/tarball/main",
            timeout=60,
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        return jsonify({"status": "error", "message": "No network connection"}), 503
    except requests.exceptions.Timeout:
        return jsonify({"status": "error", "message": "Request timed out"}), 503
    except requests.exceptions.RequestException as e:
        return jsonify({"status": "error", "message": str(e)}), 502
    try:
        buf = io.BytesIO(resp.content)
        changed = []
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            for member in tf.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                rel = parts[1]
                dest = os.path.join(update_dir, rel)
                if member.isdir():
                    os.makedirs(dest, exist_ok=True)
                elif member.isfile():
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    new_bytes = tf.extractfile(member).read()
                    try:
                        existing = open(dest, "rb").read()
                    except OSError:
                        existing = None
                    if existing != new_bytes:
                        with open(dest, "wb") as fh:
                            fh.write(new_bytes)
                        changed.append(rel)
        status = "updated" if changed else "up_to_date"
        return jsonify({"status": status, "changed": changed})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Extraction failed: {e}"}), 500


@app.route("/<path:reddit_path>")
def json_catch_all(reddit_path):
    if reddit_path.endswith(".json"):
        return _proxy_reddit(reddit_path)
    return jsonify({"error": "not found"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, threaded=True)
