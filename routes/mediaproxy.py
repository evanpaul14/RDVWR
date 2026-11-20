"""Opt-in media proxy (PROXY_MEDIA=1): serves media through the server so the browser
never contacts those hosts directly. Reddit/Imgur/Giphy CDNs are always proxyable; with
PROXY_MEDIA on, any public https host is (images/video/audio only, size-capped, and
never a private/LAN address — see core/safe_fetch.py).

/api/m/<host>/<path> mirrors the upstream URL path-for-path, so relative URIs in HLS
playlists (segments, alternate audio) resolve back through the proxy on their own.
When enabled, JSON and SPA HTML responses have allowlisted absolute media URLs
rewritten to that form in an after-request pass, which covers every route at once.
"""
import re
from urllib.parse import urljoin, urlparse
from flask import Blueprint, current_app, request, Response
from core.base_path import app_path
from reddit.client import SESSION, HEADERS
from core.helpers import STREAM_CHUNK_SIZE, log
from core.safe_fetch import build_session, is_public_hostname

bp = Blueprint("mediaproxy", __name__)

# Keep in sync with _PROXY_HOSTS in static/utils.js.
MEDIA_PROXY_HOSTS = frozenset({
    'i.redd.it', 'v.redd.it', 'preview.redd.it', 'external-preview.redd.it',
    'a.thumbs.redditmedia.com', 'b.thumbs.redditmedia.com',
    'styles.redditmedia.com', 'emoji.redditmedia.com', 'www.redditstatic.com',
    'i.imgur.com', 'media.giphy.com',
})
MEDIA_PROXY_MAX_REDIRECTS = 3
OPEN_MAX_BYTES = 200 * 1024 * 1024
_OPEN_SESSION = build_session(HEADERS)
_PLAYLIST_TYPES = ('mpegurl',)
_REDIRECT_CODES = (301, 302, 303, 307, 308)

_HOST_ALT = '|'.join(re.escape(h) for h in sorted(MEDIA_PROXY_HOSTS))
# Matches a quoted URL, plus the object key it's the value of (if any). Only the start of
# a string is rewritten, so links mid-way through text are left alone.
_BODY_URL_RE     = re.compile(rb'(?:"(\w+)"\s*:\s*)?"https://(' + _HOST_ALT.encode() + rb')/')
# Markdown/text fields: a body that is just an image URL must stay absolute so the
# markdown renderer still autolinks it (it applies the proxy itself when rendering).
_TEXT_KEYS       = frozenset({b'body', b'selftext', b'description', b'public_description',
                              b'sidebar', b'text', b'title'})
_REWRITE_TYPES   = ('application/json', 'text/html')
# Open mode: JSON fields that hold a media URL (never `url`, which may be a plain link),
# and src/poster of media tags in rendered HTML (also when escaped inside a JSON string).
_OPEN_MEDIA_KEYS = (b'preview_img', b'thumb_url', b'gif_url', b'video_url', b'hls_url', b'audio_url',
                    b'redgifs_fallback_url', b'redgifs_fallback_hls')
_OPEN_KEY_RE = re.compile(rb'("(?:' + b'|'.join(_OPEN_MEDIA_KEYS) + rb')"\s*:\s*")https://([A-Za-z0-9.-]+)/')
_OPEN_TAG_RE = re.compile(rb'(<(?:img|source|video|audio)\b[^>]*?\s(?:src|poster)=\\?")https://([A-Za-z0-9.-]+)/', re.I)
_ABS_URL_RE      = re.compile(r'https://([A-Za-z0-9.-]+)/')


def is_open_proxy_on():
    return bool(current_app.config.get('PROXY_MEDIA'))


def can_proxy_host(host):
    """Allowlisted CDN, or — with PROXY_MEDIA on — any public hostname other than this server."""
    if host in MEDIA_PROXY_HOSTS:
        return True
    if not (is_open_proxy_on() and is_public_hostname(host)):
        return False
    return not (request and request.host.split(':')[0].lower() == host.lower())


def _media_type_ok(content_type):
    ct = content_type.split(';')[0].strip().lower()
    if ct == 'image/svg+xml':
        return False
    return ct.startswith(('image/', 'video/', 'audio/')) or any(t in ct for t in _PLAYLIST_TYPES)


def _capped(chunks, limit):
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > limit:
            return
        yield chunk


def proxy_path(host, rest):
    return app_path(f"/api/m/{host}/{rest}")


@bp.route("/api/m/<host>/<path:rest>")
def proxy_media(host, rest):
    if not can_proxy_host(host):
        return ('', 403)
    url = f"https://{host}/{rest}"
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8", "replace")
    # Accept */* so i.redd.it serves the file instead of redirecting to its HTML viewer.
    headers = {**HEADERS, 'Referer': 'https://www.reddit.com/', 'Accept': '*/*'}
    if 'Range' in request.headers:
        headers['Range'] = request.headers['Range']
    try:
        # Follow redirects by hand so a hop off the allowlist is never fetched server-side.
        for _ in range(MEDIA_PROXY_MAX_REDIRECTS + 1):
            session = SESSION if urlparse(url).hostname in MEDIA_PROXY_HOSTS else _OPEN_SESSION
            upstream = session.get(url, headers=headers, stream=True, timeout=20, allow_redirects=False)
            if upstream.status_code not in _REDIRECT_CODES:
                break
            upstream.close()
            url = urljoin(url, upstream.headers.get('Location', ''))
            parsed = urlparse(url)
            if parsed.scheme != 'https' or not parsed.hostname or not can_proxy_host(parsed.hostname):
                return ('', 502)
        else:
            return ('', 502)
    except Exception as e:
        log.warning("proxy_media fetch failed host=%s: %s", host, e)
        return ('', 502)

    content_type = upstream.headers.get('Content-Type', 'application/octet-stream')
    resp_headers = {'Content-Type': content_type, 'Accept-Ranges': 'bytes',
                    'Cache-Control': 'public, max-age=604800, immutable'}
    if upstream.status_code >= 400:
        upstream.close()
        return Response(b'', status=upstream.status_code, headers={'Cache-Control': 'no-store'})

    final_host = urlparse(url).hostname
    is_open = final_host not in MEDIA_PROXY_HOSTS
    if is_open:
        if not _media_type_ok(content_type):
            upstream.close()
            return ('', 415)
        if int(upstream.headers.get('Content-Length') or 0) > OPEN_MAX_BYTES:
            upstream.close()
            return ('', 413)
        resp_headers['X-Content-Type-Options'] = 'nosniff'

    if rest.endswith('.m3u8') or 'mpegurl' in content_type.lower():
        # Playlists normally use relative URIs; rewrite any absolute ones too.
        text = _ABS_URL_RE.sub(lambda m: proxy_path(m.group(1), '') if can_proxy_host(m.group(1)) else m.group(0),
                               upstream.text)
        resp_headers['Cache-Control'] = 'public, max-age=300'
        return Response(text, status=upstream.status_code, headers=resp_headers)

    for h in ('Content-Length', 'Content-Range'):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]
    chunks = upstream.iter_content(chunk_size=STREAM_CHUNK_SIZE)
    if is_open:
        chunks = _capped(chunks, OPEN_MAX_BYTES)
    return Response(chunks, status=upstream.status_code, headers=resp_headers, direct_passthrough=True)


def _rewrite_match(m):
    key, host = m.group(1), m.group(2)
    if key in _TEXT_KEYS:
        return m.group(0)
    key_prefix = m.group(0)[:m.start(2) - m.start(0) - len(b'"https://')]
    return key_prefix + b'"' + app_path('/api/m/').encode() + host + b'/'


def _rewrite_open(m):
    host = m.group(2).decode()
    if not can_proxy_host(host):
        return m.group(0)
    return m.group(1) + app_path('/api/m/').encode() + m.group(2) + b'/'


@bp.after_app_request
def rewrite_media_urls(resp):
    """Point allowlisted media URLs in API/SPA payloads at /api/m/ when PROXY_MEDIA is on.
    Registered after Flask-Compress, so it runs before compression."""
    if not current_app.config.get('PROXY_MEDIA'):
        return resp
    if resp.direct_passthrough or resp.is_streamed or request.path.endswith('.json'):
        return resp
    if not (resp.mimetype or '').startswith(_REWRITE_TYPES) or request.path.startswith('/api/m/'):
        return resp
    body = resp.get_data()
    new_body = _BODY_URL_RE.sub(_rewrite_match, body)
    if is_open_proxy_on():
        new_body = _OPEN_KEY_RE.sub(_rewrite_open, new_body)
        new_body = _OPEN_TAG_RE.sub(_rewrite_open, new_body)
    if new_body != body:
        resp.set_data(new_body)
    return resp
