"""GET /healthz: a cheap liveness/readiness probe for uptime monitors and load balancers.
Reports upstream-auth and cache state only; no identities, tokens or cookies."""
import os
import subprocess
import sys
import time
from flask import Blueprint, jsonify
from gql import client as gql_client
from core import helpers
from reddit import client as reddit_client
from reddit import waf

bp = Blueprint("health", __name__)
_STARTED = time.time()


def _commit():
    """Short git SHA of the running checkout (tells you whether a deploy landed)."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                              text=True, timeout=2).stdout.strip() or None
    except Exception:
        return None


_COMMIT = _commit()


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _oauth_state():
    now = time.time()
    pool = reddit_client._device_pool
    valid = sum(1 for d in pool if d.token and now < d.expires_at)
    return {"enabled": reddit_client.REDDIT_OAUTH, "pool_size": len(pool), "tokens_valid": valid,
            # Tokens are minted lazily, so a fresh process legitimately has none yet.
            "ever_acquired": any(d.acquired_at for d in pool),
            # Per-token view: no token values or identities, just timing and rate-limit headroom.
            "tokens": [{"valid": bool(d.token and now < d.expires_at),
                        "expires_in_secs": max(0, round(d.expires_at - now)) if d.token else None,
                        "age_secs": round(now - d.acquired_at) if d.acquired_at else None,
                        "ratelimit_remaining": _num(getattr(d, "ratelimit_remaining", None)),
                        "gql_ratelimit_remaining": _num(getattr(d, "gql_ratelimit_remaining", None))}
                       for d in pool]}


def _gql_state():
    last = gql_client._last_refresh
    return {"hashes": len(gql_client._hashes),
            "last_github_check_secs_ago": round(time.time() - last) if last else None}


def _cache_state():
    if helpers._redis_client is None:
        return {"backend": "memory", "ok": True}
    try:
        helpers._redis_client.ping()
        return {"backend": "redis", "ok": True}
    except Exception:
        return {"backend": "redis", "ok": False}


@bp.route("/healthz")
def healthz():
    oauth, cache = _oauth_state(), _cache_state()
    degraded = (not cache["ok"]) or (
        oauth["enabled"] and oauth["ever_acquired"] and oauth["tokens_valid"] == 0)
    wafs = waf.waf_status()
    reasons = []
    if not cache["ok"]:
        reasons.append("cache unreachable")
    if oauth["enabled"] and oauth["ever_acquired"] and oauth["tokens_valid"] == 0:
        reasons.append("no valid OAuth tokens")
    # Not a failure (the process is fine and recovers on its own), but worth surfacing.
    warnings = []
    if wafs["cooling"]:
        warnings.append("Reddit WAF cooldown active: upstream calls paused")
    if wafs["blocked_endpoints"]:
        warnings.append("endpoints skipped after WAF blocks: " + ", ".join(wafs["blocked_endpoints"]))
    body = {"status": "degraded" if degraded else "ok", "reasons": reasons, "warnings": warnings,
            "uptime_secs": round(time.time() - _STARTED), "commit": _COMMIT,
            "python": sys.version.split()[0], "oauth": oauth, "waf": wafs,
            "gql": _gql_state(), "cache": cache}
    resp = jsonify(body)
    resp.status_code = 503 if degraded else 200
    resp.headers["Cache-Control"] = "no-store"
    return resp
