"""Back off from Reddit's WAF instead of retrying into it."""
import logging
import threading
import time
import requests

log = logging.getLogger("reddit.client")

# Reddit's WAF answers this IP's anonymous requests with an HTML "Blocked" page. Some
# endpoints are blocked outright (api/info, user/<u>/about); the rest get blocked for a few
# minutes once the IP looks too busy. Hammering it with retries during a block only keeps
# it going, so after a burst of blocks every upstream call pauses for a while, and a
# caller-named endpoint that's blocked outright is skipped (its fallback answers) for an hour.
_WAF_BURST_COUNT  = 3     # blocked calls ...
_WAF_BURST_WINDOW = 30    # ... within this many seconds ...
_WAF_COOLDOWN     = 45    # ... pause all anonymous upstream calls this long
_WAF_ENDPOINT_TTL = 3600  # skip a blocked endpoint (waf_key) this long before re-probing
_waf_lock        = threading.Lock()
_waf_hits        = []     # monotonic times of recent blocked calls
_waf_until       = 0.0
_waf_endpoints   = {}     # waf_key -> monotonic time its block expires


def waf_cooling():
    """True while upstream calls are paused after a burst of WAF blocks."""
    return time.monotonic() < _waf_until


def note_waf_block(waf_key=None):
    """Record a WAF-blocked call; starts the cooldown once they come in a burst."""
    global _waf_until
    now = time.monotonic()
    with _waf_lock:
        if waf_key:
            _waf_endpoints[waf_key] = now + _WAF_ENDPOINT_TTL
        _waf_hits[:] = [t for t in _waf_hits if now - t < _WAF_BURST_WINDOW] + [now]
        if len(_waf_hits) >= _WAF_BURST_COUNT and now >= _waf_until:
            _waf_until = now + _WAF_COOLDOWN
            _waf_hits.clear()
            log.warning("reddit WAF blocking this IP: pausing upstream calls for %ds", _WAF_COOLDOWN)


def waf_endpoint_blocked(waf_key):
    return bool(waf_key) and time.monotonic() < _waf_endpoints.get(waf_key, 0.0)


def waf_status():
    """Snapshot for /healthz: cooldown state and how many endpoints are skipped."""
    now = time.monotonic()
    with _waf_lock:
        blocked = sorted(k for k, t in _waf_endpoints.items() if now < t)
    return {"cooling": now < _waf_until, "cooldown_secs_left": max(0, round(_waf_until - now)),
            "blocked_endpoints": blocked}


def waf_blocked_response(url=""):
    """A stand-in for Reddit's block page, returned without touching the network."""
    resp = requests.Response()
    resp.status_code = 403
    resp.headers["content-type"] = "text/html"
    resp._content = b"Blocked (rdvwr cooldown)"
    resp.url = url
    return resp
