"""Shared constants, caching, and request helpers used across route modules."""
import os
import re
import json
import time
import logging
import threading
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from flask import jsonify, request, Response, make_response
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

# Sharing cache/rate-limit state across processes (e.g. multiple gunicorn workers or
# horizontally-scaled instances) requires an external store. Set REDIS_URL to opt in;
# with it unset (the default, single-Pi deployment), everything stays in-process as
# before. The OAuth device pool in reddit_client is NOT shared this way — each process
# keeps its own, which is harmless (just more rotating identities).
REDIS_URL = os.environ.get('REDIS_URL', '').strip()

_redis_client = None
if REDIS_URL:
    import redis as _redis_mod
    _redis_client = _redis_mod.Redis.from_url(REDIS_URL, decode_responses=False)


def _redis_cache_encode(value):
    """Cached values are either raw JSON bytes (server_cache) or plain JSON-able
    Python objects (dict/str/None from the smaller route-level caches); tag which so
    decode knows whether to json.loads it back."""
    if isinstance(value, bytes):
        return b'B' + value
    return b'J' + json.dumps(value).encode()


def _redis_cache_decode(raw):
    if raw[:1] == b'B':
        return raw[1:]
    return json.loads(raw[1:])

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
    """Thread-safe cache with a per-entry TTL and a size cap. When full, expired
    entries are swept first; only if none have expired is the oldest-inserted entry
    evicted (not true LRU, but keeps memory bounded predictably).

    In-process by default (one dict per worker). Pass `name=` to make an instance
    share state via Redis instead, when REDIS_URL is set — needed for a cache to stay
    coherent across multiple gunicorn workers or horizontally-scaled instances. Two
    TTLCache instances with the same `name` share the same Redis-backed keyspace, so
    `name` must be unique per logical cache (e.g. 'view', 'og', 'avatar')."""
    SWEEP_INTERVAL = 30  # seconds; bounds the O(n) sweep cost when the cache stays full

    def __init__(self, max_size, name=None):
        self._max_size = max_size
        self._lock = threading.Lock()
        self._data = {}
        self._last_sweep = 0.0
        self._redis = _redis_client if (_redis_client and name) else None
        self._prefix = f'ttlc:{name}:' if self._redis else None

    def get(self, key):
        """Returns the cached value, or the _CACHE_MISS sentinel if absent/expired
        (a cached value can itself legitimately be None, so plain None can't mean "miss")."""
        if self._redis:
            try:
                raw = self._redis.get(self._prefix + str(key))
            except Exception as e:
                log.warning("redis cache get failed, treating as miss: %s", e)
                return _CACHE_MISS
            return _redis_cache_decode(raw) if raw is not None else _CACHE_MISS
        now = time.time()
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] <= now:
                del self._data[key]
                return _CACHE_MISS
        return hit[1] if hit else _CACHE_MISS

    def set(self, key, value, ttl):
        if self._redis:
            try:
                self._redis.set(self._prefix + str(key), _redis_cache_encode(value), ex=ttl)
            except Exception as e:
                log.warning("redis cache set failed, dropping entry: %s", e)
            return
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


# Rate limiting is left to the reverse proxy in front of this app (nginx/Caddy/etc.)
# rather than handled in-process — see README "Rate limiting" for an nginx example.


def cached_json(data, seconds):
    resp = make_response(jsonify(data))
    resp.headers['Cache-Control'] = f'public, max-age={seconds}'
    return resp

_view_cache = TTLCache(500, name='view')

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
                # Serve the stored JSON bytes as-is rather than re-serializing the payload.
                return Response(hit, mimetype='application/json',
                                headers={'Cache-Control': f'public, max-age={ttl}'})
            resp = f(*args, **kwargs)
            cache_control = resp.headers.get('Cache-Control', '') if isinstance(resp, Response) else ''
            if (isinstance(resp, Response) and resp.status_code == 200 and resp.is_json
                    and not resp.direct_passthrough and not resp.is_streamed
                    and 'no-store' not in cache_control and 'private' not in cache_control):
                _view_cache.set(key, resp.get_data(), ttl)
            return resp
        return wrapper
    return decorator
