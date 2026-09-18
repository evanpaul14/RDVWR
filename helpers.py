"""Shared constants, caching, and request helpers used across route modules."""
import re
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
_CACHE_MISS = object()


class TTLCache:
    """Thread-safe in-process cache with a per-entry TTL and a size cap evicted
    oldest-inserted-first (not true LRU, but keeps memory bounded predictably)."""
    def __init__(self, max_size):
        self._max_size = max_size
        self._lock = threading.Lock()
        self._data = {}

    def get(self, key):
        """Returns the cached value, or the _CACHE_MISS sentinel if absent/expired
        (a cached value can itself legitimately be None, so plain None can't mean "miss")."""
        with self._lock:
            hit = self._data.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        return _CACHE_MISS

    def set(self, key, value, ttl):
        with self._lock:
            if len(self._data) >= self._max_size:
                self._data.pop(next(iter(self._data)))
            self._data[key] = (time.time() + ttl, value)

    def __len__(self):
        return len(self._data)

    def clear(self):
        with self._lock:
            self._data.clear()


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
