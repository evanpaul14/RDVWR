import os
import time
import random
import threading
import uuid as _uuid_mod
import base64
import logging
import requests
from curl_cffi import requests as cffi_requests
from reddit_app_versions import ANDROID_APP_VERSIONS

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"}


def _build_proxies():
    """Outbound proxy from HTTP(S)_PROXY / ALL_PROXY (http, https, socks4/5 schemes)."""
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
# Larger pool than the default 10 so concurrent media proxying reuses connections.
_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32)
SESSION.mount("https://", _ADAPTER)
SESSION.mount("http://", _ADAPTER)

REDDIT_OAUTH = os.environ.get('REDDIT_OAUTH', '1').strip().lower() not in ('0', 'false', 'no', 'off')

# ── Reddit OAuth spoofing ─────────────────────────────────────────────────────
_REDDIT_ANDROID_CLIENT_ID = "ohXpoqrZYub1kg"
_CFFI_PROFILES            = ["chrome120", "chrome124", "chrome131", "firefox133"]
_TOKEN_POOL_SIZE          = 3
_TOKEN_ROTATE_SECS        = 1800
# Rotate before the per-token request budget runs out and Reddit starts 429ing.
_RATELIMIT_LOW_WATERMARK  = 10


def _android_user_agent(versions=ANDROID_APP_VERSIONS):
    return f"Reddit/{random.choice(versions)}/Android {random.randint(9, 14)}"


class _OAuthDevice:
    def __init__(self):
        self.lock        = threading.Lock()
        self.token       = None
        self.expires_at  = 0.0
        self.acquired_at = 0.0
        self.extra       = {}  # loid/session headers from the auth response
        self.ratelimit_remaining = None
        self.session     = cffi_requests.Session(proxies=PROXIES)  # keep-alive per device
        self.reset_identity()

    def needs_refresh(self):
        now = time.time()
        if self.ratelimit_remaining is not None and self.ratelimit_remaining < _RATELIMIT_LOW_WATERMARK:
            return True
        return (not self.token
                or now >= self.expires_at
                or now - self.acquired_at >= _TOKEN_ROTATE_SECS)

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
        self.user_agent  = _android_user_agent()


def recent_user_agent():
    """User-Agent from a recent build, for features Reddit gates on client version
    (e.g. Devvit webviews); the device pool includes builds too old for those."""
    return _android_user_agent(ANDROID_APP_VERSIONS[:8])


def _refresh_device(device: _OAuthDevice):
    low_ratelimit = device.ratelimit_remaining
    device.reset_identity()
    log.info("token refresh: device_id=%s ua=%s low_ratelimit=%s", device.device_id, device.user_agent, low_ratelimit)
    try:
        token, expires_in, extra = _fetch_android_token(device)
        device.token       = token
        device.expires_at  = time.time() + expires_in - 120
        device.acquired_at = time.time()
        device.extra       = extra
        device.ratelimit_remaining = None  # fresh token, fresh budget
        log.info("token refresh ok: method=_fetch_android_token expires_in=%s", expires_in)
    except Exception as e:
        log.warning("token refresh failed: method=_fetch_android_token error=%s", e)


def _cffi_post(url, device, **kwargs):
    """POST via curl_cffi, falling back to requests on TLS errors."""
    try:
        return device.session.post(url, impersonate=device.impersonate, **kwargs)
    except Exception:
        # TLS handshake failure (e.g. BoringSSL TLS13_DOWNGRADE on ARM)
        if "content" in kwargs:
            kwargs["data"] = kwargs.pop("content")
        return SESSION.post(url, **kwargs)


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
        with device.lock:
            if device.needs_refresh():
                _refresh_device(device)
    return device


_quarantine_session: "requests.Session | None" = None
_quarantine_session_lock = threading.Lock()


def get_quarantine_session() -> "requests.Session":
    """Session opted into quarantined subreddits. Accepting one quarantine via
    old.reddit.com's form sets a domain-wide cookie that covers them all."""
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
            s.get(
                "https://old.reddit.com/quarantine?dest=https%3A%2F%2Fold.reddit.com%2Fr%2FTheRedPill",
                timeout=10,
            )
            s.post(
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
    remaining = resp.headers.get("x-ratelimit-remaining")
    if remaining is None:
        return
    try:
        device.ratelimit_remaining = float(remaining)
    except ValueError:
        pass


def reddit_get(url, *, quarantine=False, **kwargs):
    """GET a Reddit API URL, optionally via oauth.reddit.com with browser TLS impersonation.
    Pass quarantine=True to use the quarantine-opted-in session instead of OAuth."""
    if not REDDIT_OAUTH or quarantine:
        sess = get_quarantine_session() if quarantine else SESSION
        return sess.get(url, **kwargs)
    url = url.replace("https://www.reddit.com/", "https://oauth.reddit.com/", 1)
    url = url.replace("https://old.reddit.com/", "https://oauth.reddit.com/", 1)
    extra_headers = kwargs.pop("headers", {})
    for attempt in range(3):
        device = _get_device()
        device.drift_qos()
        headers = {**device.api_headers(), **extra_headers}
        try:
            resp = device.session.get(url, headers=headers, impersonate=device.impersonate, **kwargs)
        except Exception:
            # TLS handshake failure — fall back to plain requests
            return SESSION.get(url, headers=headers, **kwargs)
        _record_ratelimit(device, resp)
        if resp.status_code == 429:
            time.sleep(min(int(resp.headers.get("Retry-After", 5)), 5))
            continue
        if resp.status_code == 401 and attempt < 2:
            device.expires_at = 0.0  # force this device to re-auth next use
            continue
        return resp
    return resp
