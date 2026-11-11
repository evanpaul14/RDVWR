import os
import time
import random
import threading
import uuid as _uuid_mod
import base64
import logging
import requests
from reddit.budget import budgeted, request as budget_request, remaining, backoff
from curl_cffi import requests as cffi_requests
from reddit.waf import note_waf_block, waf_blocked_response, waf_cooling, waf_endpoint_blocked

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"}


def _build_proxies():
    """Forward proxy for all outbound Reddit/media requests, e.g. ALL_PROXY=socks5h://127.0.0.1:9050.
    Supports http(s):// and socks4(a)/socks5(h):// schemes, same as curl/requests."""
    all_proxy = os.environ.get('ALL_PROXY') or os.environ.get('all_proxy', '')
    http_proxy = (os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy') or all_proxy).strip()
    https_proxy = (os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or all_proxy).strip()
    proxies = {}
    if http_proxy:
        proxies['http'] = http_proxy
    if https_proxy:
        proxies['https'] = https_proxy
    return proxies


PROXIES = _build_proxies()

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.proxies.update(PROXIES)
# requests keeps only 10 idle connections per host by default; with 16 gunicorn threads
# and a feed's worth of proxied media hitting the same CDN host, the rest would be
# discarded and every request would pay a fresh TLS handshake.
_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32)
SESSION.mount("https://", _ADAPTER)
SESSION.mount("http://", _ADAPTER)

# Set by reddit_login: returns a bearer token when the current request should read as the
# logged-in Reddit account (a local request while logged in), else None.
USER_TOKEN_PROVIDER = None

REDDIT_OAUTH = os.environ.get('REDDIT_OAUTH', '1').strip().lower() not in ('0', 'false', 'no', 'off')

# ── Reddit OAuth spoofing ─────────────────────────────────────────────────────

# Real Android app builds from the last couple of months (taken from
# Nachtalb/redlib's generated oauth_resources.rs). An old build on a fresh
# token is an easy outlier for Reddit to flag, so keep this list recent.
_ANDROID_APP_VERSIONS = [
    "Version 2026.42.0/Build 2642021",
    "Version 2026.42.0/Build 2642020",
    "Version 2026.42.0/Build 2642001",
    "Version 2026.41.0/Build 2641081",
    "Version 2026.41.0/Build 2641080",
    "Version 2026.41.0/Build 2641021",
    "Version 2026.40.0/Build 2640091",
    "Version 2026.40.0/Build 2640090",
    "Version 2026.40.0/Build 2640041",
    "Version 2026.40.0/Build 2640011",
    "Version 2026.39.1/Build 2639070",
    "Version 2026.39.0/Build 2639041",
    "Version 2026.39.0/Build 2639040",
    "Version 2026.39.0/Build 2639031",
    "Version 2026.39.0/Build 2639021",
    "Version 2026.39.0/Build 2639001",
    "Version 2026.38.0/Build 2638050",
    "Version 2026.38.0/Build 2638001",
    "Version 2026.37.0/Build 2637051",
    "Version 2026.37.0/Build 2637050",
    "Version 2026.37.0/Build 2637021",
    "Version 2026.36.0/Build 2636090",
    "Version 2026.36.0/Build 2636081",
    "Version 2026.35.0/Build 2635091",
    "Version 2026.35.0/Build 2635071",
    "Version 2026.35.0/Build 2635001",
    "Version 2026.34.0/Build 2634011",
    "Version 2026.33.2/Build 2633220",
]
_REDDIT_ANDROID_CLIENT_ID = "ohXpoqrZYub1kg"
# TLS fingerprints Reddit still serves (per Nachtalb/redlib's probes, Nov 2026):
# Chrome 124-131 and Safari 17-26. Reddit blocks Chrome 132+, every Firefox and
# OkHttp. Profiles the installed curl_cffi doesn't know are dropped below.
_CFFI_WANTED = [
    "chrome124", "chrome131", "chrome131_android",
    "safari170", "safari172_ios", "safari180", "safari180_ios",
    "safari184", "safari184_ios", "safari260", "safari260_ios", "safari2601",
]


def _supported_profiles(wanted):
    try:
        from curl_cffi.requests import BrowserType
        known = {b.value for b in BrowserType}
    except Exception:
        return ["chrome124", "chrome131"]
    return [p for p in wanted if p in known] or ["chrome124", "chrome131"]


_CFFI_PROFILES            = _supported_profiles(_CFFI_WANTED)
_TOKEN_POOL_SIZE          = 3
# Keep each token (and its device identity) until it nears expiry (~24h), like
# the real app and redlib do. Minting a fresh device every few minutes from one
# IP looks nothing like a phone.
# Preemptively rotate a device once Reddit reports its per-token request budget is
# this low, rather than waiting for it to run out and get 429s (mirrors redlib's
# OAUTH_RATELIMIT_REMAINING approach).
_RATELIMIT_LOW_WATERMARK = 30


class _OAuthDevice:
    def __init__(self):
        self.lock        = threading.Lock()
        self.token       = None
        self.expires_at  = 0.0
        self.acquired_at = 0.0
        self.device_id   = str(_uuid_mod.uuid4())
        self.impersonate = random.choice(_CFFI_PROFILES)
        self.qos         = random.uniform(1.0, 100.0)
        app_ver          = random.choice(_ANDROID_APP_VERSIONS)
        android_v        = random.randint(12, 16)
        self.user_agent  = f"Reddit/{app_ver}/Android {android_v}"
        self.extra        = {}  # loid, session headers from auth response
        self.ratelimit_remaining = None  # last x-ratelimit-remaining seen for this token
        # Reused across requests (thread-local curl handle under the hood) so
        # repeated calls for this device get TCP/TLS connection keep-alive
        # instead of a fresh handshake every time.
        self.session     = cffi_requests.Session(proxies=PROXIES)

    def needs_refresh(self):
        now = time.time()
        if self.ratelimit_remaining is not None and self.ratelimit_remaining < _RATELIMIT_LOW_WATERMARK:
            return True
        return not self.token or now >= self.expires_at

    def api_headers(self):
        codecs = "available-codecs=video/avc, video/hevc"
        if random.random() < 0.5:
            codecs += ", video/x-vnd.on2.vp9"
        pairs = [
            ("User-Agent",            self.user_agent),
            ("Authorization",         f"Bearer {self.token}" if self.token else ""),
            ("x-reddit-retry",        "algo=no-retries"),
            ("x-reddit-compression",  "1"),
            ("x-reddit-qos",          f"{self.qos:.3f}"),
            ("x-reddit-media-codecs", codecs),
            ("client-vendor-id",      self.device_id),
            ("X-Reddit-Device-Id",    self.device_id),
        ]
        pairs.extend(self.extra.items())
        random.shuffle(pairs)
        return dict(pairs)

    def drift_qos(self):
        self.qos = max(1.0, min(100.0, self.qos + random.gauss(0, 3)))

    def reset_identity(self):
        self.device_id   = str(_uuid_mod.uuid4())
        self.impersonate = random.choice(_CFFI_PROFILES)
        self.qos         = random.uniform(1.0, 100.0)
        app_ver          = random.choice(_ANDROID_APP_VERSIONS)
        android_v        = random.randint(12, 16)
        self.user_agent  = f"Reddit/{app_ver}/Android {android_v}"


def recent_user_agent():
    """A User-Agent from a recent Android app build.

    Reddit gates some server-rendered features (e.g. Devvit2 webview
    entrypoints) behind a minimum client version and silently falls back to
    an "app unavailable" placeholder for older ones, so pick from the newest
    builds for requests that need a current client.
    """
    app_ver   = random.choice(_ANDROID_APP_VERSIONS[:8])
    android_v = random.randint(12, 16)
    return f"Reddit/{app_ver}/Android {android_v}"


def _refresh_device(device: _OAuthDevice):
    low_ratelimit = device.ratelimit_remaining
    device.reset_identity()
    log.info("token refresh: device_id=%s ua=%s low_ratelimit=%s", device.device_id, device.user_agent, low_ratelimit)
    try:
        token, expires_in, extra = _fetch_android_token(device)
        device.token       = token
        # Refresh a few minutes early, jittered so the pool doesn't renew at once.
        device.expires_at  = time.time() + expires_in - random.uniform(120, 900)
        device.acquired_at = time.time()
        device.extra       = extra
        device.ratelimit_remaining = None  # fresh token, fresh budget
        log.info("token refresh ok: method=_fetch_android_token expires_in=%s", expires_in)
    except Exception as e:
        log.warning("token refresh failed: method=_fetch_android_token error=%s", e)


def _cffi_post(url, device, **kwargs):
    """POST via curl_cffi, falling back to requests on TLS errors."""
    try:
        return budget_request(device.session.post, url, impersonate=device.impersonate, **kwargs)
    except Exception as e:
        # TLS handshake failure (e.g. BoringSSL TLS13_DOWNGRADE on ARM) — use requests
        kwargs.pop("impersonate", None)
        if "content" in kwargs:
            kwargs["data"] = kwargs.pop("content")
        return budget_request(SESSION.post, url, **kwargs)


def _fetch_android_token(device: _OAuthDevice):
    auth = base64.b64encode(f"{_REDDIT_ANDROID_CLIENT_ID}:".encode()).decode()
    resp = _cffi_post(
        "https://www.reddit.com/auth/v2/oauth/access-token/loid",
        device,
        headers={
            "User-Agent":            device.user_agent,
            "Authorization":         f"Basic {auth}",
            "x-reddit-retry":        "algo=no-retries",
            "x-reddit-compression":  "1",
            "x-reddit-qos":          f"{device.qos:.3f}",
            "x-reddit-media-codecs": "available-codecs=video/avc, video/hevc",
            "client-vendor-id":      device.device_id,
            "X-Reddit-Device-Id":    device.device_id,
            "Content-Type":          "application/json; charset=UTF-8",
        },
        json={"scopes": ["*", "email", "pii"]},
        timeout=10,
    )
    if not resp.ok:
        log.warning("android token HTTP %s: %s", resp.status_code, resp.text[:200])
    resp.raise_for_status()
    data  = resp.json()
    extra = {}
    if "x-reddit-loid" in resp.headers:
        extra["x-reddit-loid"]    = resp.headers["x-reddit-loid"]
    if "x-reddit-session" in resp.headers:
        extra["x-reddit-session"] = resp.headers["x-reddit-session"]
    return data["access_token"], data["expires_in"], extra



_device_pool   = [_OAuthDevice() for _ in range(_TOKEN_POOL_SIZE)]
_pool_counter  = 0
_pool_lock     = threading.Lock()


def _get_device() -> _OAuthDevice:
    global _pool_counter
    with _pool_lock:
        idx = _pool_counter % _TOKEN_POOL_SIZE
        _pool_counter += 1
    device = _device_pool[idx]
    if device.needs_refresh():
        if not device.lock.acquire(timeout=remaining(10)):
            raise requests.exceptions.Timeout("Token refresh wait exceeded budget")
        try:
            if device.needs_refresh():
                _refresh_device(device)
        finally:
            device.lock.release()
    return device


_quarantine_session: "requests.Session | None" = None
_quarantine_session_lock = threading.Lock()


def _get_quarantine_session() -> "requests.Session":
    """Return a requests.Session pre-opted into quarantined subreddits.
    Initialised once via old.reddit.com's form-based quarantine acceptance flow,
    which sets the _options cookie with pref_quarantine_optin=true at the
    reddit.com domain level (covers all quarantined subreddits)."""
    global _quarantine_session
    if _quarantine_session is not None:
        return _quarantine_session
    with _quarantine_session_lock:
        if _quarantine_session is not None:
            return _quarantine_session
        s = requests.Session()
        s.headers.update(HEADERS)
        s.proxies.update(PROXIES)
        try:
            budget_request(s.get,
                "https://old.reddit.com/quarantine?dest=https%3A%2F%2Fold.reddit.com%2Fr%2FTheRedPill",
                timeout=10,
            )
            budget_request(s.post,
                "https://old.reddit.com/quarantine",
                data={"sr_name": "TheRedPill", "dest": "https://old.reddit.com/r/TheRedPill", "accept": "yes"},
                allow_redirects=False,
                timeout=10,
            )
        except Exception as e:
            log.warning("quarantine session init failed: %s", e)
        _quarantine_session = s
    return _quarantine_session


def _record_ratelimit(device: _OAuthDevice, resp):
    """Track Reddit's per-token request budget from the response headers, so
    needs_refresh() can rotate this device before it actually runs dry."""
    remaining = resp.headers.get("x-ratelimit-remaining")
    if remaining is None:
        return
    try:
        device.ratelimit_remaining = float(remaining)
    except ValueError:
        pass


@budgeted
def reddit_get(url, *, quarantine=False, rotate_on_403=False, waf_key=None, **kwargs):
    """GET a Reddit API URL, optionally via oauth.reddit.com with browser TLS impersonation.
    Pass quarantine=True to use the quarantine-opted-in session instead of OAuth.
    Pass rotate_on_403=True to retry once on a fresh token and identity when Reddit answers
    403 (often a WAF block tied to one device token); a second 403 is returned as is.
    Pass waf_key for an endpoint Reddit may block outright: once it does, the anonymous
    path skips it (answering with the block page) for an hour so the caller's fallback runs."""
    if not quarantine and USER_TOKEN_PROVIDER:
        user_token = USER_TOKEN_PROVIDER()
        if user_token:
            user_url = url.replace("https://www.reddit.com/", "https://oauth.reddit.com/", 1)
            headers = {**kwargs.get("headers", {}), "Authorization": f"Bearer {user_token}"}
            try:
                resp = budget_request(cffi_requests.get, user_url, impersonate="chrome131", proxies=PROXIES,
                                         **{**kwargs, "headers": headers})
                if resp.status_code != 401:
                    return resp
            except Exception as e:
                log.warning("account read failed, using anonymous path: %s", e)
    if not REDDIT_OAUTH or quarantine:
        sess = _get_quarantine_session() if quarantine else SESSION
        return budget_request(sess.get, url, **kwargs)
    url = url.replace("https://www.reddit.com/", "https://oauth.reddit.com/", 1)
    url = url.replace("https://old.reddit.com/", "https://oauth.reddit.com/", 1)
    if waf_cooling() or waf_endpoint_blocked(waf_key):
        return waf_blocked_response(url)
    extra_headers = kwargs.pop("headers", {})
    rotated = False
    for attempt in range(3):
        device = _get_device()
        device.drift_qos()
        headers = {**device.api_headers(), **extra_headers}
        try:
            resp = budget_request(device.session.get, url, headers=headers, impersonate=device.impersonate, **kwargs)
        except Exception as e:
            # TLS handshake failure (e.g. BoringSSL TLS13_DOWNGRADE on ARM) — use requests
            return budget_request(SESSION.get, url, headers=headers, **kwargs)
        _record_ratelimit(device, resp)
        if resp.status_code == 429:
            backoff(min(int(resp.headers.get("Retry-After", 5)), 5))
            continue
        if resp.status_code == 403 and rotate_on_403 and not rotated and attempt < 2:
            rotated = True
            log.warning("reddit 403 on %s via device %s; cycling token and retrying",
                        url, device.device_id[:8])
            device.reset_identity()
            device.expires_at = 0.0  # force this device to re-auth with its new identity
            continue
        if resp.status_code == 403:
            ctype = resp.headers.get("content-type") or ""
            log.warning("reddit 403 on %s via device %s (%s, %s) content-type=%r attempt=%d",
                        url, device.device_id[:8], device.impersonate, device.user_agent, ctype, attempt + 1)
            if "json" not in ctype:
                # The WAF's HTML block page. It's mostly per-IP, so one more device is worth
                # a try for an ordinary endpoint, but not a hard-blocked one or a third try.
                if attempt == 0 and not waf_key and not waf_cooling():
                    continue
                note_waf_block(waf_key)
                return resp
        if resp.status_code == 401 and attempt < 2:
            device.expires_at = 0.0  # force this device to re-auth next use
            continue
        return resp
    return resp
