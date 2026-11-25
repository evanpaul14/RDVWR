"""Shared request helpers for the route modules: allowlist regexes, upstream errors,
param validation, the same-site gate, and batch post fetching.

Settings and allowed sort/time values live in core/config.py, caching in core/cache.py,
URL checks in core/urls.py."""
import re
import secrets
import logging
import contextvars
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from urllib.parse import urlsplit
from flask import jsonify, request
from core.config import DEFAULT_SETTINGS, TIME_FILTERS
from parsing.media_detection import process_post
from reddit.client import reddit_get


FEED_LIMIT           = 25
COMMENTS_LIMIT       = 200
STREAM_CHUNK_SIZE    = 65536
REDGIFS_TOKEN_TTL    = 23 * 3600

log = logging.getLogger(__name__)


SUBREDDIT_RE = re.compile(r'^[A-Za-z0-9_]{1,50}(?:\+[A-Za-z0-9_]{1,50}){0,49}$')
USERNAME_RE  = re.compile(r'^[A-Za-z0-9_-]{1,50}$')
POST_ID_RE   = re.compile(r'^[A-Za-z0-9]{1,10}$')
# Comment permalinks can carry 13-character IDs; posts use the shorter limit.
COMMENT_ID_RE = re.compile(r'^[A-Za-z0-9]{1,13}$')
MULTINAME_RE = re.compile(r'^[A-Za-z0-9_]{1,50}$')


def feed_time(sort, t):
    """The time window for a subreddit-style feed: `t` when valid, else the default (today
    unless RDVWR_DEFAULT_SUB_TIME says otherwise), because Reddit reads a missing `t` on
    top/controversial as all time. "" for sorts without a time window."""
    if sort not in ("top", "controversial"):
        return ""
    return t if t in TIME_FILTERS else DEFAULT_SETTINGS["subTime"]


def add_time_param(params, sort, t, sorts_with_time=("top", "controversial")):
    """Reddit only honors the `t` (time-window) param for certain sorts (top/controversial
    by default; some endpoints only support it for "top")."""
    if sort in sorts_with_time and t in TIME_FILTERS:
        params["t"] = t


class UpstreamError(Exception):
    """Raised by the shared fetch_* helpers in routes/ when Reddit (or another upstream)
    answers with something other than the requested data. `state` optionally classifies
    the failure (e.g. a subreddit's "quarantined"/"private"), for callers that branch on it.
    The /api/* endpoints turn it into a JSON error via .response(); the server-rendered
    noscript pages show .message instead."""

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
        """Generic error for a non-200 upstream response."""
        if status_code == 404:
            return cls(not_found, 404)
        return cls(f"Reddit returned {status_code}", status_code)


def parallel(*fns):
    """Run each zero-arg callable in its own thread and return results in order."""
    # Each thread runs in a copy of this context so the request (and with it the local
    # Reddit login, see reddit_login.request_account_active) is visible inside fetches.
    with ThreadPoolExecutor(max_workers=len(fns)) as ex:
        futures = [ex.submit(contextvars.copy_context().run, fn) for fn in fns]
        return [f.result() for f in futures]


def is_waf_block(resp):
    """Reddit's "Blocked" page: a 403 (or 429) that isn't JSON, unlike a private subreddit's."""
    return resp.status_code in (403, 429) and "json" not in resp.headers.get("content-type", "")


def fetch_posts_by_id(ids):
    """{post id: raw REST-shaped post dict} for these ids, {} when they can't be fetched.
    Reddit's WAF blocks the device tokens outright on api/info.json and on by_id with more
    than one id, so only a single id goes to REST (by_id); several ids, or a blocked one,
    go to the app's GraphQL API."""
    resp = None
    if len(ids) == 1:
        resp = reddit_get(f"https://www.reddit.com/by_id/t3_{ids[0]}.json",
                          params={'raw_json': 1}, timeout=8)
    if resp is None or is_waf_block(resp):
        try:
            from gql.posts import fetch_gql_posts_by_id   # imported here: gql_posts imports helpers
            return fetch_gql_posts_by_id(ids)
        except Exception as e:
            log.warning("gql posts-by-id failed: %s", e)
            return {}
    if not resp.ok:
        return {}
    return {c['data']['id']: c['data'] for c in resp.json()['data']['children']}


def hydrate_linked_posts(posts):
    """Batch-fetch full data (title/media) for plain link-posts that point at another
    Reddit post, so they can be rendered with the same embed as real crossposts."""
    targets = [p for p in posts if p.get('linked_post')][:100]
    if not targets:
        return
    ids = [p['linked_post']['id'] for p in targets]
    try:
        by_id = fetch_posts_by_id(ids)
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
# --- Same-site gate for /api/* ----------------------------------------------------
# The frontend serves `<meta name="referrer" content="no-referrer">` (templates/index.html),
# so browser-issued requests never carry a Referer, and plain <img>/<video src>/top-level-
# navigation requests (media proxy, downloads) never carry Origin either — only JS fetch()
# calls do. Sec-Fetch-Site is sent by all modern browsers on every request type regardless
# of Referrer-Policy, so it's the primary signal; Origin/Referer are the fallback for the
# rare browser that omits it. A request with none of the three is either a very old browser
# or a non-browser client (curl, a script) — treated as disallowed either way.
SAME_SITE_VALUES = {'same-origin', 'same-site'}


def is_same_site_request():
    sec_fetch_site = request.headers.get('Sec-Fetch-Site')
    if sec_fetch_site is not None:
        return sec_fetch_site in SAME_SITE_VALUES
    # Double-submit cookie fallback: app.js echoes the rdvwr_csrf cookie back as a
    # header on every fetch() it makes (see static/app.js). Some browser privacy
    # hardening (Firefox's privacy.resistFingerprinting) strips Sec-Fetch-Site,
    # Origin, and Referer from every request, which would otherwise be
    # indistinguishable from a non-browser client here. A cross-site page can't read
    # this origin's cookie to forge the matching header, regardless of SameSite.
    csrf_cookie = request.cookies.get('rdvwr_csrf')
    csrf_header = request.headers.get('X-Rdvwr-Fetch')
    if csrf_cookie and csrf_header and secrets.compare_digest(csrf_cookie, csrf_header):
        return True
    # Hostname-only comparison for the Origin/Referer fallback: a reverse proxy in
    # front of Flask (e.g. nginx's `proxy_set_header Host $host;`) commonly strips
    # the port from the Host header it forwards, while a browser's Origin/Referer
    # always includes it — comparing full netlocs would then 403 every same-site
    # request on a non-default port. The port isn't part of what this check is
    # protecting against (a different origin's JS calling the API), so dropping it
    # from the comparison doesn't weaken it.
    host = urlsplit(f'//{request.host}').hostname
    origin = request.headers.get('Origin')
    if origin is not None:
        return urlsplit(origin).hostname == host
    referer = request.headers.get('Referer')
    if referer is not None:
        return urlsplit(referer).hostname == host
    return False
