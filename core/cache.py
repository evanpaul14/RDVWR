"""Response and fetch caching: TTLCache, the @server_cache / @fetch_cache decorators,
and cached_json(). In-process by default; set REDIS_URL to share named caches across
processes (multiple gunicorn workers or horizontally-scaled instances). The OAuth device
pool in reddit_client is NOT shared this way — each process keeps its own, which is
harmless (just more rotating identities)."""
import copy
import json
import logging
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from functools import wraps
from inspect import signature

import requests
from flask import Response, jsonify, make_response, request

from core import stats
from core.config import REDIS_URL
from reddit import login as reddit_login
from reddit.budget import remaining

log = logging.getLogger(__name__)

CACHE_TTL_STATIC     = 604800   # 1 week
CACHE_TTL_FEED       = 300
CACHE_TTL_SUBREDDIT  = 600

redis_client = None
if REDIS_URL:
    import redis as _redis_mod
    redis_client = _redis_mod.Redis.from_url(REDIS_URL, decode_responses=False)


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


CACHE_MISS = object()


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
        self._redis = redis_client if (redis_client and name) else None
        self._prefix = f'ttlc:{name}:' if self._redis else None

    def get(self, key):
        """Returns the cached value, or the CACHE_MISS sentinel if absent/expired
        (a cached value can itself legitimately be None, so plain None can't mean "miss")."""
        if self._redis:
            try:
                raw = self._redis.get(self._prefix + str(key))
            except Exception as e:
                log.warning("redis cache get failed, treating as miss: %s", e)
                return CACHE_MISS
            return _redis_cache_decode(raw) if raw is not None else CACHE_MISS
        now = time.time()
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] <= now:
                del self._data[key]
                return CACHE_MISS
        return hit[1] if hit else CACHE_MISS

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


def cached_json(data, seconds):
    resp = make_response(jsonify(data))
    # Data fetched as the logged-in account (vote state etc.) must never reach a shared cache.
    resp.headers['Cache-Control'] = ('private, no-store' if reddit_login.request_account_active() or not seconds
                                     else f'public, max-age={seconds}')
    return resp


_view_cache = TTLCache(500, name='view')
_inflight = {}
_inflight_lock = threading.Lock()
INFLIGHT_WAIT = 30  # seconds a follower waits on the request already fetching its key


def server_cache(ttl):
    """Cache a view's JSON payload in-process for `ttl` seconds, keyed by full
    request path+query, so identical requests (repeat visits, multiple tabs)
    don't re-hit Reddit within the window. Concurrent misses for one key are
    coalesced: one request fetches, the rest wait for it and read the cache."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if reddit_login.request_account_active():
                # Reading as the account: bypass the shared cache in both directions.
                resp = f(*args, **kwargs)
                if isinstance(resp, Response):
                    resp.headers['Cache-Control'] = 'private, no-store'
                return resp
            key = request.full_path
            hit = _view_cache.get(key)
            stats.incr('view_cache_hit' if hit is not CACHE_MISS else 'view_cache_miss')
            if hit is not CACHE_MISS:
                # Serve the stored JSON bytes as-is rather than re-serializing the payload.
                return Response(hit, mimetype='application/json',
                                headers={'Cache-Control': f'public, max-age={ttl}'})
            with _inflight_lock:
                leader = _inflight.get(key)
                if leader is None:
                    _inflight[key] = leader = threading.Event()
                    is_leader = True
                else:
                    is_leader = False
            if not is_leader:
                leader.wait(INFLIGHT_WAIT)
                hit = _view_cache.get(key)
                if hit is not CACHE_MISS:
                    return Response(hit, mimetype='application/json',
                                    headers={'Cache-Control': f'public, max-age={ttl}'})
                # The leader failed or its response wasn't cacheable: fetch for ourselves.
                return f(*args, **kwargs)
            try:
                resp = f(*args, **kwargs)
                cache_control = resp.headers.get('Cache-Control', '') if isinstance(resp, Response) else ''
                if (isinstance(resp, Response) and resp.status_code == 200 and resp.is_json
                        and not resp.direct_passthrough and not resp.is_streamed
                        and 'no-store' not in cache_control and 'private' not in cache_control):
                    _view_cache.set(key, resp.get_data(), ttl)
                return resp
            finally:
                with _inflight_lock:
                    _inflight.pop(key, None)
                leader.set()
        return wrapper
    return decorator


_fetch_cache = TTLCache(500)
_fetch_inflight = {}
_fetch_inflight_lock = threading.Lock()


def fetch_cache(ttl, empty_ttl=None, skip=None):
    """Memoize a shared fetch_* helper's return value in-process for `ttl` seconds, so the
    server-rendered pages get the same caching the /api/* endpoints have. `timeout` is left
    out of the key; calls made as the logged-in account bypass the cache. Exceptions are
    never cached. A falsy result is held for `empty_ttl` instead when given (for best-effort
    fetchers that return [] on failure), and `skip(result)` can veto caching. Callers get a
    private copy, so they can mutate what they receive."""
    def decorator(f):
        sig = signature(f)
        @wraps(f)
        def wrapper(*args, **kwargs):
            if reddit_login.request_account_active():
                return f(*args, **kwargs)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            key = (f.__module__, f.__qualname__, tuple((k, v) for k, v in bound.arguments.items() if k != 'timeout'))
            # Coordinate the cache lookup and leader election to avoid a miss racing
            # with another leader publishing its completed result.
            with _fetch_inflight_lock:
                hit = _fetch_cache.get(key)
                if hit is not CACHE_MISS:
                    future = None
                else:
                    future = _fetch_inflight.get(key)
                    leader = future is None
                    if leader:
                        _fetch_inflight[key] = future = Future()
            stats.incr('fetch_cache_hit' if hit is not CACHE_MISS else
                       'fetch_cache_miss' if leader else 'fetch_cache_coalesced')
            if hit is not CACHE_MISS:
                return copy.deepcopy(hit)
            if not leader:
                try:
                    result = future.result(timeout=remaining(min(INFLIGHT_WAIT, bound.arguments.get('timeout', INFLIGHT_WAIT))))
                except FutureTimeout as exc:
                    raise requests.exceptions.Timeout('Timed out waiting for shared upstream fetch') from exc
                return copy.deepcopy(result)
            try:
                result = f(*args, **kwargs)
                snapshot = copy.deepcopy(result)
                if not (skip and skip(result)):
                    hold = empty_ttl if (empty_ttl is not None and not result) else ttl
                    if hold:
                        _fetch_cache.set(key, snapshot, hold)
                future.set_result(snapshot)
                return result
            except BaseException as exc:
                future.set_exception(exc)
                raise
            finally:
                with _fetch_inflight_lock:
                    _fetch_inflight.pop(key, None)
        return wrapper
    return decorator
