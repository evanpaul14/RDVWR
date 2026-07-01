import os
import time
import random
import threading
import uuid as _uuid_mod
import base64
import logging
import requests
from curl_cffi import requests as cffi_requests

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

REDDIT_OAUTH = os.environ.get('REDDIT_OAUTH', '1').strip().lower() not in ('0', 'false', 'no', 'off')

# ── Reddit OAuth spoofing ─────────────────────────────────────────────────────

_ANDROID_APP_VERSIONS = [
    # 2026 (YYWW*1000 scheme)
    "Version 2026.26.0/Build 2626111",
    "Version 2026.25.0/Build 2625001",
    "Version 2026.24.0/Build 2624001",
    "Version 2026.23.0/Build 2623001",
    "Version 2026.22.0/Build 2622001",
    "Version 2026.21.0/Build 2621001",
    "Version 2026.20.0/Build 2620001",
    "Version 2026.19.0/Build 2619001",
    "Version 2026.18.0/Build 2618001",
    "Version 2026.17.0/Build 2617001",
    "Version 2026.16.0/Build 2616001",
    "Version 2026.15.0/Build 2615001",
    "Version 2026.14.0/Build 2614001",
    "Version 2026.13.0/Build 2613001",
    "Version 2026.12.0/Build 2612001",
    "Version 2026.11.0/Build 2611001",
    "Version 2026.10.0/Build 2610111",
    "Version 2026.09.0/Build 2609001",
    "Version 2026.08.0/Build 2608001",
    "Version 2026.07.0/Build 2607001",
    "Version 2026.06.0/Build 2606001",
    "Version 2026.05.0/Build 2605001",
    "Version 2026.04.0/Build 2604001",
    "Version 2026.03.0/Build 2603001",
    "Version 2026.02.0/Build 2602001",
    "Version 2026.01.0/Build 2601001",
    # 2025 (YYWW*1000 scheme from week 7 onward)
    "Version 2025.52.0/Build 2552001",
    "Version 2025.51.0/Build 2551001",
    "Version 2025.50.0/Build 2550001",
    "Version 2025.49.0/Build 2549001",
    "Version 2025.48.0/Build 2548011",
    "Version 2025.47.0/Build 2547001",
    "Version 2025.46.0/Build 2546001",
    "Version 2025.45.0/Build 2545001",
    "Version 2025.44.0/Build 2544001",
    "Version 2025.43.0/Build 2543001",
    "Version 2025.42.0/Build 2542001",
    "Version 2025.41.0/Build 2541001",
    "Version 2025.40.0/Build 2540011",
    "Version 2025.39.0/Build 2539001",
    "Version 2025.38.0/Build 2538001",
    "Version 2025.37.0/Build 2537001",
    "Version 2025.36.0/Build 2536001",
    "Version 2025.35.0/Build 2535001",
    "Version 2025.34.0/Build 2534001",
    "Version 2025.33.0/Build 2533001",
    "Version 2025.32.0/Build 2532001",
    "Version 2025.31.0/Build 2531001",
    "Version 2025.30.0/Build 2530001",
    "Version 2025.29.0/Build 2529001",
    "Version 2025.28.0/Build 2528001",
    "Version 2025.27.0/Build 2527001",
    "Version 2025.26.0/Build 2526001",
    "Version 2025.25.0/Build 2525001",
    "Version 2025.24.0/Build 2524020",
    "Version 2025.23.0/Build 2523001",
    "Version 2025.22.0/Build 2522001",
    "Version 2025.21.0/Build 2521001",
    "Version 2025.20.0/Build 2520001",
    "Version 2025.19.0/Build 2519001",
    "Version 2025.18.0/Build 2518001",
    "Version 2025.17.0/Build 2517001",
    "Version 2025.16.0/Build 2516001",
    "Version 2025.15.0/Build 2515001",
    "Version 2025.14.0/Build 2514001",
    "Version 2025.13.0/Build 2513001",
    "Version 2025.12.0/Build 2512001",
    "Version 2025.11.0/Build 2511120",
    "Version 2025.10.0/Build 2510001",
    "Version 2025.09.0/Build 2509021",
    "Version 2025.08.0/Build 2508011",
    "Version 2025.07.0/Build 2507001",
    # 2025 early (sequential scheme)
    "Version 2025.06.0/Build 2221631",
    "Version 2025.04.0/Build 2181092",
    "Version 2025.02.0/Build 2138572",
    "Version 2025.01.0/Build 2118801",
    # 2024 (sequential scheme)
    "Version 2024.48.0/Build 2040898",
    "Version 2024.47.0/Build 2029755",
    "Version 2024.46.0/Build 2012731",
    "Version 2024.45.0/Build 2001943",
    "Version 2024.44.0/Build 1988458",
    "Version 2024.43.0/Build 1972250",
    "Version 2024.42.0/Build 1952440",
    "Version 2024.41.1/Build 1947805",
    "Version 2024.41.0/Build 1941199",
    "Version 2024.40.0/Build 1928580",
    "Version 2024.39.0/Build 1916713",
    "Version 2024.38.0/Build 1902791",
    "Version 2024.37.0/Build 1888053",
    "Version 2024.36.0/Build 1875012",
    "Version 2024.35.0/Build 1861437",
    "Version 2024.34.0/Build 1837909",
    "Version 2024.33.0/Build 1819908",
    "Version 2024.32.1/Build 1813258",
    "Version 2024.32.0/Build 1809095",
    "Version 2024.31.0/Build 1786202",
    "Version 2024.30.0/Build 1770787",
    "Version 2024.28.1/Build 1741165",
    "Version 2024.28.0/Build 1737665",
    "Version 2024.26.1/Build 1717435",
    "Version 2024.26.0/Build 1710470",
    "Version 2024.25.3/Build 1703490",
    "Version 2024.25.2/Build 1700401",
    "Version 2024.25.0/Build 1693595",
    "Version 2024.24.1/Build 1682520",
    "Version 2024.23.1/Build 1665606",
    "Version 2024.22.1/Build 1652272",
    "Version 2024.22.0/Build 1645257",
    "Version 2024.21.0/Build 1631686",
    "Version 2024.20.3/Build 1624970",
    "Version 2024.20.2/Build 1624969",
    "Version 2024.20.1/Build 1615586",
    "Version 2024.20.0/Build 1612800",
    "Version 2024.19.0/Build 1593346",
    "Version 2024.18.1/Build 1585304",
    "Version 2024.18.0/Build 1577901",
    "Version 2024.17.0/Build 1568106",
    "Version 2024.16.0/Build 1551366",
    "Version 2024.15.0/Build 1536823",
    "Version 2024.14.0/Build 1520556",
    "Version 2024.13.0/Build 1505187",
    "Version 2024.12.0/Build 1494694",
    "Version 2024.11.0/Build 1480707",
    "Version 2024.10.1/Build 1478645",
    "Version 2024.10.0/Build 1470045",
    "Version 2024.08.0/Build 1439531",
    "Version 2024.07.0/Build 1429651",
    "Version 2024.06.0/Build 1418489",
    "Version 2024.05.0/Build 1403584",
    "Version 2024.04.0/Build 1391236",
    "Version 2024.03.0/Build 1379408",
    "Version 2024.02.0/Build 1368985",
    "Version 2023.50.1/Build 1345844",
    "Version 2023.50.0/Build 1332338",
    "Version 2023.49.1/Build 1322281",
    "Version 2023.49.0/Build 1321715",
    "Version 2023.48.0/Build 1319123",
    "Version 2023.47.0/Build 1303604",
    "Version 2023.45.0/Build 1281371",
    "Version 2023.44.0/Build 1268622",
    "Version 2023.43.0/Build 1257426",
    "Version 2023.42.0/Build 1245088",
    "Version 2023.41.1/Build 1239615",
    "Version 2023.41.0/Build 1233125",
    "Version 2023.40.0/Build 1221521",
    "Version 2023.39.1/Build 1221505",
    "Version 2023.39.0/Build 1211607",
    "Version 2023.38.0/Build 1198522",
    "Version 2023.37.0/Build 1182743",
    "Version 2023.36.0/Build 1168982",
    "Version 2023.35.0/Build 1157967",
    "Version 2023.34.0/Build 1144243",
    "Version 2023.33.1/Build 1129741",
    "Version 2023.32.1/Build 1114141",
    "Version 2023.32.0/Build 1109919",
    "Version 2023.31.0/Build 1091027",
    "Version 2023.30.0/Build 1078734",
    "Version 2023.29.0/Build 1059855",
    "Version 2023.28.0/Build 1046887",
    "Version 2023.27.0/Build 1031923",
    "Version 2023.26.0/Build 1019073",
    "Version 2023.25.1/Build 1018737",
    "Version 2023.25.0/Build 1014750",
    "Version 2023.24.0/Build 998541",
    "Version 2023.23.0/Build 983896",
    "Version 2023.22.0/Build 968223",
    "Version 2023.21.0/Build 956283",
    "Version 2023.20.1/Build 946732",
    "Version 2023.20.0/Build 943980",
    "Version 2023.19.0/Build 927681",
    "Version 2023.18.0/Build 911877",
    "Version 2023.17.1/Build 900542",
    "Version 2023.17.0/Build 896030",
    "Version 2023.16.1/Build 886269",
    "Version 2023.16.0/Build 883294",
    "Version 2023.15.0/Build 870628",
    "Version 2023.14.1/Build 864826",
    "Version 2023.14.0/Build 861593",
    "Version 2023.13.0/Build 852246",
    "Version 2023.12.0/Build 841150",
    "Version 2023.11.0/Build 830610",
    "Version 2023.10.0/Build 821148",
    "Version 2023.09.1/Build 816833",
    "Version 2023.09.0/Build 812015",
    "Version 2023.08.0/Build 798718",
    "Version 2023.07.1/Build 790267",
    "Version 2023.07.0/Build 788827",
    "Version 2023.06.0/Build 775017",
    "Version 2023.05.0/Build 755453",
    "Version 2023.04.0/Build 744681",
    "Version 2023.03.0/Build 729220",
    "Version 2023.02.0/Build 717912",
    "Version 2023.01.0/Build 709875",
    "Version 2022.45.0/Build 677985",
    "Version 2022.44.0/Build 664348",
    "Version 2022.43.0/Build 648277",
    "Version 2022.42.0/Build 638508",
    "Version 2022.41.1/Build 634168",
    "Version 2022.41.0/Build 630468",
    "Version 2022.40.0/Build 624782",
    "Version 2022.39.1/Build 619019",
    "Version 2022.39.0/Build 615385",
    "Version 2022.38.0/Build 607460",
    "Version 2022.37.0/Build 601691",
    "Version 2022.36.0/Build 593102",
    "Version 2022.35.1/Build 589034",
    "Version 2022.35.0/Build 588016",
    "Version 2022.34.0/Build 579352",
    "Version 2022.33.0/Build 572600",
    "Version 2022.32.0/Build 567875",
    "Version 2022.31.1/Build 562612",
    "Version 2022.31.0/Build 556666",
    "Version 2022.30.0/Build 548620",
    "Version 2022.28.0/Build 533235",
    "Version 2022.27.1/Build 529687",
    "Version 2022.27.0/Build 527406",
    "Version 2022.26.0/Build 521193",
    "Version 2022.25.2/Build 519915",
    "Version 2022.25.1/Build 516394",
    "Version 2022.25.0/Build 515072",
    "Version 2022.24.1/Build 513462",
    "Version 2022.24.0/Build 510950",
    "Version 2022.23.1/Build 506606",
    "Version 2022.23.0/Build 502374",
    "Version 2022.22.0/Build 498700",
    "Version 2022.21.0/Build 492436",
    "Version 2022.20.0/Build 487703",
]
_REDDIT_ANDROID_CLIENT_ID = "ohXpoqrZYub1kg"
_CFFI_PROFILES            = ["chrome120", "chrome124", "chrome131", "firefox133"]
_TOKEN_POOL_SIZE          = 3
_TOKEN_ROTATE_SECS        = 1800  # rotate device identity every 30 min


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
        android_v        = random.randint(9, 14)
        self.user_agent  = f"Reddit/{app_ver}/Android {android_v}"
        self.extra        = {}  # loid, session headers from auth response

    def needs_refresh(self):
        now = time.time()
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
        app_ver          = random.choice(_ANDROID_APP_VERSIONS)
        android_v        = random.randint(9, 14)
        self.user_agent  = f"Reddit/{app_ver}/Android {android_v}"


def _refresh_device(device: _OAuthDevice):
    device.reset_identity()
    log.info("token refresh: device_id=%s ua=%s", device.device_id, device.user_agent)
    try:
        token, expires_in, extra = _fetch_android_token(device)
        device.token       = token
        device.expires_at  = time.time() + expires_in - 120
        device.acquired_at = time.time()
        device.extra       = extra
        log.info("token refresh ok: method=_fetch_android_token expires_in=%s", expires_in)
    except Exception as e:
        log.warning("token refresh failed: method=_fetch_android_token error=%s", e)


def _cffi_post(url, device, **kwargs):
    """POST via curl_cffi, falling back to requests on TLS errors."""
    try:
        return cffi_requests.post(url, impersonate=device.impersonate, **kwargs)
    except Exception as e:
        # TLS handshake failure (e.g. BoringSSL TLS13_DOWNGRADE on ARM) — use requests
        log.debug("cffi POST TLS fallback url=%s: %s", url, e)
        kwargs.pop("impersonate", None)
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


def reddit_get(url, **kwargs):
    """GET a Reddit API URL, optionally via oauth.reddit.com with browser TLS impersonation."""
    if not REDDIT_OAUTH:
        return SESSION.get(url, **kwargs)
    url = url.replace("https://www.reddit.com/", "https://oauth.reddit.com/", 1)
    url = url.replace("https://old.reddit.com/", "https://oauth.reddit.com/", 1)
    extra_headers = kwargs.pop("headers", {})
    for attempt in range(3):
        device = _get_device()
        device.drift_qos()
        headers = {**device.api_headers(), **extra_headers}
        try:
            resp = cffi_requests.get(url, headers=headers, impersonate=device.impersonate, **kwargs)
        except Exception as e:
            # TLS handshake failure (e.g. BoringSSL TLS13_DOWNGRADE on ARM) — use requests
            log.debug("cffi GET TLS fallback url=%s: %s", url, e)
            return SESSION.get(url, headers=headers, **kwargs)
        if resp.status_code == 429:
            time.sleep(min(int(resp.headers.get("Retry-After", 5)), 5))
            continue
        if resp.status_code == 401 and attempt < 2:
            device.expires_at = 0.0  # force this device to re-auth next use
            continue
        return resp
    return resp
