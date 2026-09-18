"""Link-preview (og:image), Devvit custom-post embeds, and translation."""
import re
import json
import socket
import ipaddress
import html as html_lib
from urllib.parse import urlparse, urlunparse
from flask import Blueprint, jsonify, request, make_response
from reddit_client import SESSION, HEADERS, _get_device, recent_user_agent
from helpers import TTLCache, _CACHE_MISS, cached_json, log

bp = Blueprint("embeds", __name__)


OG_IMAGE_RE         = re.compile(r'<meta[^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])[^>]*content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])', re.I)
OG_DESC_RE          = re.compile(r'<meta[^>]+(?:property=["\']og:description["\']|name=["\'](?:twitter:description|description)["\'])[^>]*content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property=["\']og:description["\']|name=["\'](?:twitter:description|description)["\'])', re.I)
_og_cache = TTLCache(1000)
OG_CACHE_TTL = 365 * 86400  # effectively permanent; entries are evicted by size cap, not expiry


@bp.route("/api/translate")
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


@bp.route("/api/og-image")
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
    cached = _og_cache.get(url)
    if cached is not _CACHE_MISS:
        return cached_json(cached, 3600)
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
        _og_cache.set(url, result, OG_CACHE_TTL)
        return cached_json(result, 3600)
    except Exception as e:
        log.warning("get_og_image failed url=%s: %s", url, e)
        result = {"url": None, "description": None}
        _og_cache.set(url, result, OG_CACHE_TTL)
        return cached_json(result, 60)


_DEVVIT_URL_RE = re.compile(r'^https://www\.reddit\.com/r/[^/]+/comments/[^/]+/[^/]+/?$')
_devvit_cache = TTLCache(200)
DEVVIT_CACHE_TTL = 3600  # the embedded signedRequestContext JWT is only valid ~24h; keep this well under that

@bp.route("/api/devvit")
def get_devvit_embed():
    """Fetch the Devvit webview entrypoint URL (and bridge context) for a custom post."""
    permalink = request.args.get('url', '').strip()
    if not permalink or not _DEVVIT_URL_RE.match(permalink):
        return jsonify({'error': 'Invalid URL'}), 400
    cached = _devvit_cache.get(permalink)
    if cached is not _CACHE_MISS:
        resp = make_response(jsonify(cached))
        resp.headers['Cache-Control'] = 'private, no-store'
        return resp
    try:
        device = _get_device()
        hdrs = {**device.api_headers(), 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
                'User-Agent': recent_user_agent()}
        r = device.session.get(permalink, impersonate=device.impersonate,
                               headers=hdrs, timeout=20, allow_redirects=True)
        m = re.search(r'<devvit2-surface[^>]+\binit="([^"]+)"', r.text, re.I)
        if not m:
            result = {'embedded': False}
        else:
            init = json.loads(html_lib.unescape(m.group(1)))
            entry = init.get('entrypointUrl', '')
            if not entry or 'devvit.net' not in entry or '/faceplant/' in entry:
                result = {'embedded': False}
            else:
                height = (init.get('postStyles') or {}).get('heightPixels', 512)
                # The Devvit webview SDK reads its render context (post id, subreddit,
                # signed auth token, poll/app state) from a JSON blob in the URL hash
                # when there's no parent-frame postMessage handshake to supply it —
                # forward what Reddit already embedded in the page so the app renders
                # instead of failing with "Invalid poll payload. Open a valid trial post."
                bridge = {k: init[k] for k in (
                    'signedRequestContext', 'postData', 'webViewClientData',
                    'viewMode', 'appPermissionState',
                ) if init.get(k) is not None}
                result = {'embedded': True, 'url': entry, 'height': int(height), 'bridge': bridge}
        _devvit_cache.set(permalink, result, DEVVIT_CACHE_TTL)
        resp = make_response(jsonify(result))
        resp.headers['Cache-Control'] = 'private, no-store'
        return resp
    except Exception as e:
        log.error('devvit embed error url=%s: %s', permalink, e)
        return jsonify({'embedded': False}), 200
