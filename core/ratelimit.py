"""Per-client request rate limiting for the whole app.

Fixed one-minute windows counted per client IP. Two buckets:
  - every request (pages, /api/*, media proxy), RATE_LIMIT per minute
  - downloads (/api/download*, which can run ffmpeg), RATE_LIMIT_DOWNLOADS per minute
Set either to 0 to turn that bucket off. Static files and /healthz are never counted.

Counters live in-process (one set per gunicorn worker) unless REDIS_URL is set, in which
case they're shared through Redis like the response cache.
"""
import ipaddress
import logging
import os
import threading
import time

from flask import jsonify, make_response, request

from core.helpers import _redis_client

log = logging.getLogger(__name__)


def _int_env(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        log.warning("Ignoring invalid %s=%r (expected a whole number)", name, os.environ.get(name))
        return default


RATE_LIMIT = _int_env('RATE_LIMIT', 600)
RATE_LIMIT_DOWNLOADS = _int_env('RATE_LIMIT_DOWNLOADS', 20)
WINDOW = 60  # seconds

_EXEMPT_PREFIXES = ('/static/', '/healthz', '/favicon')
_DOWNLOAD_PREFIX = '/api/download'
# Headers a reverse proxy on this machine (nginx, Caddy, cloudflared) sets to the real
# client address. Only read when the connection itself comes from a loopback/private
# address, so a client talking to the app directly can't pick its own bucket.
_PROXY_IP_HEADERS = ('CF-Connecting-IP', 'X-Real-IP')
_FORWARD_HEADERS = ('X-Forwarded-For', 'X-Real-IP', 'Forwarded', 'CF-Connecting-IP')

_lock = threading.Lock()
_counts = {}  # (bucket, ip, window) -> count
_last_sweep = 0


def _ip(addr):
    try:
        return ipaddress.ip_address(addr)
    except ValueError:
        return None


def _is_internal(addr):
    ip = _ip(addr)
    return bool(ip and (ip.is_loopback or ip.is_private))


def client_ip():
    remote = request.remote_addr or ''
    if not _is_internal(remote):
        return remote
    for header in _PROXY_IP_HEADERS:
        value = (request.headers.get(header) or '').strip()
        if value:
            return value
    # The proxy appends the address it saw to X-Forwarded-For, so the last entry is the
    # one it vouches for; anything before it came from the client and can be forged.
    xff = request.headers.get('X-Forwarded-For') or ''
    last = xff.split(',')[-1].strip()
    return last or remote


def _hit(bucket, ip, window):
    """Count one request and return the window's total so far."""
    if _redis_client:
        key = f'rl:{bucket}:{ip}:{window}'
        try:
            pipe = _redis_client.pipeline()
            pipe.incr(key)
            pipe.expire(key, WINDOW + 5)
            return pipe.execute()[0]
        except Exception as e:
            log.warning("redis rate-limit counter failed, allowing request: %s", e)
            return 0
    global _last_sweep
    with _lock:
        if window != _last_sweep:
            # New window: drop the counters from earlier ones so memory stays bounded.
            for k in [k for k in _counts if k[2] < window]:
                del _counts[k]
            _last_sweep = window
        key = (bucket, ip, window)
        _counts[key] = _counts.get(key, 0) + 1
        return _counts[key]


def _limited(retry_after):
    if request.path.startswith('/api/'):
        resp = jsonify({"error": "Too many requests, slow down."})
    else:
        resp = make_response("Too many requests. Wait a minute and try again.\n")
        resp.mimetype = 'text/plain'
    resp.status_code = 429
    resp.headers['Retry-After'] = str(retry_after)
    return resp


def check():
    path = request.path
    if path.startswith(_EXEMPT_PREFIXES):
        return None
    remote = request.remote_addr or ''
    # A request made on the server itself, not through a proxy (local use, an SSH tunnel).
    ip = _ip(remote)
    if ip and ip.is_loopback and not any(h in request.headers for h in _FORWARD_HEADERS):
        return None
    ip = client_ip()
    now = time.time()
    window = int(now // WINDOW)
    retry_after = max(1, int((window + 1) * WINDOW - now))
    buckets = [('all', RATE_LIMIT)]
    if path.startswith(_DOWNLOAD_PREFIX):
        buckets.append(('dl', RATE_LIMIT_DOWNLOADS))
    for bucket, limit in buckets:
        if limit and _hit(bucket, ip, window) > limit:
            log.warning("rate limit 429 bucket=%s ip=%s path=%s", bucket, ip, path)
            return _limited(retry_after)
    return None


def reset():
    """Clear the in-process counters (tests)."""
    global _last_sweep
    with _lock:
        _counts.clear()
        _last_sweep = 0


def register(app):
    app.before_request(check)
