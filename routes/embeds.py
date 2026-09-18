"""Link-preview (og:image), Devvit custom-post embeds, and translation."""
import re
import json
import socket
import ipaddress
import html as html_lib
from urllib.parse import urljoin, urlparse, urlunparse
from flask import Blueprint, jsonify, request, make_response
from reddit_client import SESSION, HEADERS, _get_device, recent_user_agent
from helpers import TTLCache, _CACHE_MISS, cached_json, error_response, log

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
    except Exception:
        return error_response(502)


_REDIRECT_CODES = (301, 302, 303, 307, 308)
OG_MAX_REDIRECTS = 3
OG_FAIL_CACHE_TTL = 600  # transient fetch failures are retried after 10 min


def _ip_allowed(addr):
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    # is_global excludes private, loopback, link-local, CGNAT (100.64/10, Tailscale),
    # 0.0.0.0/8 and other reserved ranges.
    return addr.is_global and not addr.is_multicast


def _resolve_ssrf_safe(hostname: str):
    """Resolve hostname and verify every address it maps to is public. Returns an
    IPv4 address string to connect to, or None if any address is disallowed."""
    try:
        infos = socket.getaddrinfo(hostname, None)
        addrs = {ipaddress.ip_address(info[4][0].split('%', 1)[0]) for info in infos}
    except Exception:
        return None
    if not addrs or not all(_ip_allowed(a) for a in addrs):
        return None
    v4 = sorted(str(a) for a in addrs if a.version == 4)
    return v4[0] if v4 else str(next(iter(addrs)))


def _og_fetch(url):
    """GET url, following up to OG_MAX_REDIRECTS redirects by hand so every hop is
    re-checked against the SSRF allowlist. Returns the final open response, or None
    if a hop is disallowed."""
    for _ in range(OG_MAX_REDIRECTS + 1):
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if parsed.scheme not in ("http", "https") or not hostname:
            return None
        resolved_ip = _resolve_ssrf_safe(hostname)
        if not resolved_ip:
            return None
        # For HTTP, connect directly to the resolved IP to prevent DNS rebinding TOCTOU.
        # For HTTPS, SSL certificate validation prevents rebinding (cert won't match a spoofed IP).
        if parsed.scheme == "http":
            ip_host = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
            safe_netloc = parsed.netloc.replace(hostname, ip_host, 1)
            fetch_url = urlunparse(parsed._replace(netloc=safe_netloc))
            fetch_headers = {**HEADERS, "Accept": "text/html", "Host": parsed.netloc}
        else:
            fetch_url = url
            fetch_headers = {**HEADERS, "Accept": "text/html"}
        r = SESSION.get(fetch_url, timeout=8, stream=True, headers=fetch_headers,
                        allow_redirects=False)
        if r.status_code not in _REDIRECT_CODES:
            return r
        location = r.headers.get("Location")
        r.close()
        if not location:
            return None
        url = urljoin(url, location)
    return None


@bp.route("/api/og-image")
def get_og_image():
    url = request.args.get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"error": "Invalid URL"}), 400
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        return jsonify({"error": "Invalid URL"}), 400
    if not hostname:
        return jsonify({"error": "Invalid URL"}), 400
    if not _resolve_ssrf_safe(hostname):
        return jsonify({"error": "URL not allowed"}), 403
    cached = _og_cache.get(url)
    if cached is not _CACHE_MISS:
        return cached_json(cached, 3600)
    try:
        r = _og_fetch(url)
        if r is None:
            return jsonify({"error": "URL not allowed"}), 403
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
        log.warning("get_og_image failed host=%s: %s", hostname, e)
        result = {"url": None, "description": None}
        _og_cache.set(url, result, OG_FAIL_CACHE_TTL)
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
