"""Shared constants, caching, and request helpers used across route modules."""
import re
import time
import logging
import threading
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from flask import current_app, jsonify, request, Response, make_response
from media_detection import process_post
from reddit_client import reddit_get


CACHE_TTL_STATIC     = 604800   # 1 week
CACHE_TTL_FEED       = 300
CACHE_TTL_SUBREDDIT  = 600
REDGIFS_TOKEN_TTL    = 23 * 3600
FEED_LIMIT           = 25
COMMENTS_LIMIT       = 200
STREAM_CHUNK_SIZE    = 65536


log = logging.getLogger(__name__)

SUBREDDIT_RE = re.compile(r'^[A-Za-z0-9_]{1,50}(?:\+[A-Za-z0-9_]{1,50}){0,49}$')
USERNAME_RE  = re.compile(r'^[A-Za-z0-9_-]{1,50}$')
POST_ID_RE   = re.compile(r'^[A-Za-z0-9]{1,10}$')
MULTINAME_RE = re.compile(r'^[A-Za-z0-9_]{1,50}$')
FEED_SORTS   = {'best', 'hot', 'new', 'top', 'rising', 'controversial'}


TIME_FILTERS = {"hour", "day", "week", "month", "year", "all"}


def add_time_param(params, sort, t, sorts_with_time=("top", "controversial")):
    """Reddit only honors the `t` (time-window) param for certain sorts (top/controversial
    by default; some endpoints only support it for "top")."""
    if sort in sorts_with_time and t in TIME_FILTERS:
        params["t"] = t


def parallel(*fns):
    """Run each zero-arg callable in its own thread and return results in order."""
    with ThreadPoolExecutor(max_workers=len(fns)) as ex:
        futures = [ex.submit(fn) for fn in fns]
        return [f.result() for f in futures]


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
                except Exception as e:
                    log.warning("linked-post process_post failed id=%s: %s", raw.get('id'), e)
    except Exception as e:
        log.warning("linked-post batch-fetch failed: %s", e)


def error_response(status=500, message="Upstream request failed"):
    """Call from inside an `except` block: logs the traceback server-side and returns
    an opaque JSON error, so exception text (upstream URLs, internals) never reaches clients."""
    log.exception("%s %s failed", request.method, request.path)
    return jsonify({"error": message}), status


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
_CACHE_MISS = object()


class TTLCache:
    """Thread-safe in-process cache with a per-entry TTL and a size cap. When full,
    expired entries are swept first; only if none have expired is the oldest-inserted
    entry evicted (not true LRU, but keeps memory bounded predictably)."""
    SWEEP_INTERVAL = 30  # seconds; bounds the O(n) sweep cost when the cache stays full

    def __init__(self, max_size):
        self._max_size = max_size
        self._lock = threading.Lock()
        self._data = {}
        self._last_sweep = 0.0

    def get(self, key):
        """Returns the cached value, or the _CACHE_MISS sentinel if absent/expired
        (a cached value can itself legitimately be None, so plain None can't mean "miss")."""
        now = time.time()
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] <= now:
                del self._data[key]
                return _CACHE_MISS
        return hit[1] if hit else _CACHE_MISS

    def set(self, key, value, ttl):
        now = time.time()
        with self._lock:
            self._data.pop(key, None)
            if len(self._data) >= self._max_size and now - self._last_sweep >= self.SWEEP_INTERVAL:
                self._last_sweep = now
                self._data = {k: v for k, v in self._data.items() if v[0] > now}
            if len(self._data) >= self._max_size:
                self._data.pop(next(iter(self._data)))
            self._data[key] = (now + ttl, value)

    def __len__(self):
        return len(self._data)

    def clear(self):
        with self._lock:
            self._data.clear()


class RateLimiter:
    """Thread-safe fixed-window request counter keyed by client. In-process only, so
    with N gunicorn workers the effective limit is up to N× the configured one."""
    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self._lock = threading.Lock()
        self._hits = {}

    def hit(self, key):
        """Count one request; returns 0 if allowed, else seconds until the window resets."""
        now = time.time()
        bucket = int(now // self.window)
        with self._lock:
            if len(self._hits) > 10000:
                self._hits = {k: v for k, v in self._hits.items() if v[0] == bucket}
            b, count = self._hits.get(key, (bucket, 0))
            count = count + 1 if b == bucket else 1
            self._hits[key] = (bucket, count)
        if count > self.limit:
            return int((bucket + 1) * self.window - now) + 1
        return 0


# (path prefix, limiter) — first match wins. Media proxies get a high ceiling since a
# single feed page loads dozens of proxied previews and video range requests.
RATE_LIMITS = [
    ('/api/download',        RateLimiter(10, 60)),
    ('/api/img',             RateLimiter(600, 60)),
    ('/api/redgifs/media/',  RateLimiter(600, 60)),
    ('/api/',                RateLimiter(240, 60)),
]


def client_ip():
    ip = request.remote_addr or ''
    # Behind the local nginx proxy every request comes from loopback; only then trust
    # its X-Real-IP header (a direct client could otherwise spoof it).
    if ip in ('127.0.0.1', '::1'):
        ip = request.headers.get('X-Real-IP', ip)
    return ip


def rate_limit():
    """before_request hook: 429 once a client exceeds its tier's limit."""
    if not current_app.config.get('RATE_LIMIT', True):
        return None
    for prefix, limiter in RATE_LIMITS:
        if request.path.startswith(prefix):
            retry_after = limiter.hit(client_ip())
            if retry_after:
                resp = jsonify({"error": "Too many requests"})
                resp.status_code = 429
                resp.headers['Retry-After'] = str(retry_after)
                return resp
            return None
    return None


def cached_json(data, seconds):
    resp = make_response(jsonify(data))
    resp.headers['Cache-Control'] = f'public, max-age={seconds}'
    return resp

_view_cache = TTLCache(1000)

def server_cache(ttl):
    """Cache a view's JSON payload in-process for `ttl` seconds, keyed by full
    request path+query, so identical requests (repeat visits, multiple tabs)
    don't re-hit Reddit within the window."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            key = request.full_path
            hit = _view_cache.get(key)
            if hit is not _CACHE_MISS:
                return cached_json(hit, ttl)
            resp = f(*args, **kwargs)
            cache_control = resp.headers.get('Cache-Control', '') if isinstance(resp, Response) else ''
            if isinstance(resp, Response) and resp.status_code == 200 and 'no-store' not in cache_control and 'private' not in cache_control:
                data = resp.get_json(silent=True)
                if data is not None:
                    _view_cache.set(key, data, ttl)
            return resp
        return wrapper
    return decorator
