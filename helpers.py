"""Shared constants, caching, and request helpers used across route modules."""
import os
import re
import json
import time
import secrets
import logging
import threading
import requests
from functools import wraps
from urllib.parse import urlsplit
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


def _bool_env(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


DISABLE_DOWNLOADS = _bool_env('DISABLE_DOWNLOADS', False)
# Hides the Reddit-cookies setting and makes /api/home ignore any cookie sent.
DISABLE_PERSONALIZED_HOME = _bool_env('RDVWR_DISABLE_PERSONALIZED_HOME', False)

# Opt-in shared cache store for multi-process deployments; unset keeps caches in-process.
REDIS_URL = os.environ.get('REDIS_URL', '').strip()

_redis_client = None
if REDIS_URL:
    import redis as _redis_mod
    _redis_client = _redis_mod.Redis.from_url(REDIS_URL, decode_responses=False)


def _redis_cache_encode(value):
    """Tag raw bytes (server_cache) vs JSON-able objects so decode can restore either."""
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
SUB_SORTS    = FEED_SORTS - {'best'}
TIME_FILTERS = {"hour", "day", "week", "month", "year", "all"}
COMMENT_SORTS = {'confidence', 'top', 'new', 'controversial', 'old', 'qa'}
SEARCH_SORTS  = {'relevance', 'hot', 'top', 'new'}
THEMES        = {'dark', 'light', 'system'}
LAYOUTS       = {'card', 'compact', 'minimal'}


def add_time_param(params, sort, t, sorts_with_time=("top", "controversial")):
    """Reddit only honors the `t` time window for some sorts."""
    if sort in sorts_with_time and t in TIME_FILTERS:
        params["t"] = t


def listing_params(after='', limit=FEED_LIMIT, sort='', t=''):
    """Paging params for a Reddit listing; `sort`/`t` only add the time window."""
    params = {"limit": limit, "raw_json": 1}
    add_time_param(params, sort, t)
    if after:
        params["after"] = after
    return params


def _enum_env(name, default, allowed):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    if raw in allowed:
        return raw
    log.warning("Ignoring invalid %s=%r (expected one of %s)", name, raw, ', '.join(sorted(allowed)))
    return default


# Deployer overrides for settings.js DEFAULTS (same keys); a visitor's saved settings win.
DEFAULT_SETTINGS = {
    'theme':             _enum_env('RDVWR_DEFAULT_THEME', 'dark', THEMES),
    'layout':            _enum_env('RDVWR_DEFAULT_LAYOUT', 'card', LAYOUTS),
    'subSort':           _enum_env('RDVWR_DEFAULT_SUB_SORT', 'hot', SUB_SORTS),
    'subTime':           _enum_env('RDVWR_DEFAULT_SUB_TIME', 'day', TIME_FILTERS),
    'commentSort':       _enum_env('RDVWR_DEFAULT_COMMENT_SORT', 'confidence', COMMENT_SORTS),
    'homeFeed':          _enum_env('RDVWR_DEFAULT_HOME_FEED', 'personalized', {'personalized', 'subscribed'}),
    'pagination':        _bool_env('RDVWR_DEFAULT_PAGINATION', False),
    'showAvatars':       _bool_env('RDVWR_DEFAULT_SHOW_AVATARS', False),
    'linkExternalMedia': _bool_env('RDVWR_DEFAULT_LINK_EXTERNAL_MEDIA', False),
    'nsfwBlur':          _bool_env('RDVWR_DEFAULT_NSFW_BLUR', False),
    'nsfwHide':          _bool_env('RDVWR_DEFAULT_NSFW_HIDE', False),
    'nsfwSearchHide':    _bool_env('RDVWR_DEFAULT_NSFW_SEARCH_HIDE', False),
    'markRead':          _bool_env('RDVWR_DEFAULT_MARK_READ', True),
    'hideReadHome':      _bool_env('RDVWR_DEFAULT_HIDE_READ_HOME', False),
    'hideReadSub':       _bool_env('RDVWR_DEFAULT_HIDE_READ_SUB', False),
}


class UpstreamError(Exception):
    """Raised by fetch_* helpers when an upstream request fails. `state` optionally
    classifies it (e.g. "quarantined"/"private"). /api/* returns .response(); noscript
    pages show .message."""

    def __init__(self, message, status=502, state=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.state = state

    def response(self):
        body = {"error": self.message}
        if self.state:
            body["state"] = self.state
        return jsonify(body), self.status

    @classmethod
    def from_status(cls, status_code, not_found="Not found"):
        if status_code == 404:
            return cls(not_found, 404)
        return cls(f"Reddit returned {status_code}", status_code)


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
    """Call from an `except` block: logs the traceback, returns an opaque JSON error."""
    log.exception("%s %s failed", request.method, request.path)
    return jsonify({"error": message}), status


def json_or_error(fetch, ttl):
    """Serve fetch()'s result as cacheable JSON, mapping failures to JSON errors."""
    try:
        return cached_json(fetch(), ttl)
    except UpstreamError as e:
        return e.response()
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception:
        return error_response(500)


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
    """Thread-safe TTL cache with a size cap. When full, expired entries are swept,
    then the oldest-inserted entry is evicted.

    Pass a unique `name` to back it with Redis when REDIS_URL is set."""
    SWEEP_INTERVAL = 30  # seconds; bounds the O(n) sweep cost when the cache stays full

    def __init__(self, max_size, name=None):
        self._max_size = max_size
        self._lock = threading.Lock()
        self._data = {}
        self._last_sweep = 0.0
        self._redis = _redis_client if (_redis_client and name) else None
        self._prefix = f'ttlc:{name}:' if self._redis else None

    def get(self, key):
        """Return the value, or _CACHE_MISS (None is a valid cached value)."""
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


SAME_SITE_VALUES = {'same-origin', 'same-site'}


def is_same_site_request():
    """Sec-Fetch-Site first; then the double-submit cookie (for browsers that strip
    fetch metadata, e.g. Firefox resistFingerprinting); then Origin/Referer.
    A request carrying none of these is treated as a non-browser client."""
    sec_fetch_site = request.headers.get('Sec-Fetch-Site')
    if sec_fetch_site is not None:
        return sec_fetch_site in SAME_SITE_VALUES
    csrf_cookie = request.cookies.get('rdvwr_csrf')
    csrf_header = request.headers.get('X-Rdvwr-Fetch')
    if csrf_cookie and csrf_header and secrets.compare_digest(csrf_cookie, csrf_header):
        return True
    # Compare hostnames only: reverse proxies often drop the port from Host.
    host = urlsplit(f'//{request.host}').hostname
    origin = request.headers.get('Origin')
    if origin is not None:
        return urlsplit(origin).hostname == host
    referer = request.headers.get('Referer')
    if referer is not None:
        return urlsplit(referer).hostname == host
    return False


def cached_json(data, seconds):
    resp = make_response(jsonify(data))
    resp.headers['Cache-Control'] = f'public, max-age={seconds}'
    return resp


_view_cache = TTLCache(500, name='view')


def server_cache(ttl):
    """Cache a view's 200 JSON body for `ttl` seconds, keyed by path+query."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            key = request.full_path
            hit = _view_cache.get(key)
            if hit is not _CACHE_MISS:
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
