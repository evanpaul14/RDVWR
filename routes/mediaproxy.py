"""Opt-in media proxy (PROXY_MEDIA=1): serves Reddit/Imgur/Giphy media through the
server so the browser never contacts those CDNs directly.

/api/m/<host>/<path> mirrors the upstream URL path-for-path, so relative URIs in HLS
playlists (segments, alternate audio) resolve back through the proxy on their own.
When enabled, JSON and SPA HTML responses have allowlisted absolute media URLs
rewritten to that form in an after-request pass, which covers every route at once.
"""
import re
from urllib.parse import urljoin, urlparse
from flask import Blueprint, current_app, request, Response
from reddit_client import SESSION, HEADERS
from helpers import STREAM_CHUNK_SIZE, log

bp = Blueprint("mediaproxy", __name__)

# Keep in sync with _PROXY_HOSTS in static/utils.js.
MEDIA_PROXY_HOSTS = frozenset({
    'i.redd.it', 'v.redd.it', 'preview.redd.it', 'external-preview.redd.it',
    'a.thumbs.redditmedia.com', 'b.thumbs.redditmedia.com',
    'styles.redditmedia.com', 'emoji.redditmedia.com', 'www.redditstatic.com',
    'i.imgur.com', 'media.giphy.com',
})
MEDIA_PROXY_MAX_REDIRECTS = 3
_REDIRECT_CODES = (301, 302, 303, 307, 308)

_HOST_ALT = '|'.join(re.escape(h) for h in sorted(MEDIA_PROXY_HOSTS))
# Only rewrite URLs that start a JSON string value, so links inside markdown bodies
# (which the frontend proxies itself when it renders them as media) stay intact.
_BODY_URL_RE     = re.compile(rb'"https://(' + _HOST_ALT.encode() + rb')/')
_PLAYLIST_URL_RE = re.compile(r'https://(' + _HOST_ALT + r')/')
_REWRITE_TYPES   = ('application/json', 'text/html')


def proxy_path(host, rest):
    return f"/api/m/{host}/{rest}"


@bp.route("/api/m/<host>/<path:rest>")
def proxy_media(host, rest):
    if host not in MEDIA_PROXY_HOSTS:
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
            upstream = SESSION.get(url, headers=headers, stream=True, timeout=20, allow_redirects=False)
            if upstream.status_code not in _REDIRECT_CODES:
                break
            upstream.close()
            url = urljoin(url, upstream.headers.get('Location', ''))
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname not in MEDIA_PROXY_HOSTS:
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

    if rest.endswith('.m3u8') or 'mpegurl' in content_type.lower():
        # Playlists normally use relative URIs; rewrite any absolute ones too.
        text = _PLAYLIST_URL_RE.sub(lambda m: proxy_path(m.group(1), ''), upstream.text)
        resp_headers['Cache-Control'] = 'public, max-age=300'
        return Response(text, status=upstream.status_code, headers=resp_headers)

    for h in ('Content-Length', 'Content-Range'):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]
    return Response(upstream.iter_content(chunk_size=STREAM_CHUNK_SIZE),
                    status=upstream.status_code, headers=resp_headers, direct_passthrough=True)


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
    new_body = _BODY_URL_RE.sub(rb'"/api/m/\1/', body)
    if new_body != body:
        resp.set_data(new_body)
    return resp
