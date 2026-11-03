"""GET /healthz: a cheap liveness/readiness probe for uptime monitors and load balancers.
Reports upstream-auth and cache state only; no identities, tokens or cookies."""
import time
from flask import Blueprint, jsonify
from gql import client as gql_client
from core import helpers
from reddit import client as reddit_client

bp = Blueprint("health", __name__)


def _oauth_state():
    now = time.time()
    pool = reddit_client._device_pool
    valid = sum(1 for d in pool if d.token and now < d.expires_at)
    return {"enabled": reddit_client.REDDIT_OAUTH, "pool_size": len(pool), "tokens_valid": valid,
            # Tokens are minted lazily, so a fresh process legitimately has none yet.
            "ever_acquired": any(d.acquired_at for d in pool)}


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
    body = {"status": "degraded" if degraded else "ok", "oauth": oauth,
            "gql": _gql_state(), "cache": cache}
    resp = jsonify(body)
    resp.status_code = 503 if degraded else 200
    resp.headers["Cache-Control"] = "no-store"
    return resp
