"""Reddit-style ".json" URLs (e.g. /r/pics/hot.json), proxied straight to Reddit's API."""
import re
from flask import Blueprint, Response, jsonify, request
from reddit_client import reddit_get
from helpers import log

bp = Blueprint("passthrough", __name__)

# Allowlist for the raw ".json" passthrough: only the same public content shapes rdvwr's
# own endpoints already expose (subreddit feeds/about/wiki, post permalinks, user
# profiles, search) — never arbitrary oauth.reddit.com paths like /api/v1/me.json or
# /api/morechildren.json, which would let any visitor use the server's pooled OAuth
# credentials as an open proxy into Reddit's authenticated API.
_JSON_PASSTHROUGH_RE = re.compile(
    r'^(?:'
    r'r/[A-Za-z0-9_+]{1,100}(?:/(?:hot|new|top|rising|controversial'
    r'|about|about/rules|about/moderators'
    r'|comments/[A-Za-z0-9]{1,10}(?:/[^/]*(?:/[A-Za-z0-9]{1,10})?)?'
    r'|duplicates/[A-Za-z0-9]{1,10}'
    r'|wiki(?:/[A-Za-z0-9_\-/]+)?))?'
    r'|u(?:ser)?/[A-Za-z0-9_-]{1,50}(?:/(?:about|submitted|comments|overview|trophies))?'
    r'|search'
    r')\.json$'
)


@bp.before_app_request
def _json_passthrough():
    """Runs ahead of routing, so a ".json" URL never falls into one of the HTML page
    routes (e.g. /user/<username>) that would otherwise also match it."""
    path = request.path.lstrip('/')
    if not path.endswith('.json') or path.startswith(('api/', 'static/')):
        return None
    if not _JSON_PASSTHROUGH_RE.match(path):
        return jsonify({"error": "not found"}), 404
    url = f"https://oauth.reddit.com/{path}"
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8")
    try:
        resp = reddit_get(url, timeout=15)
    except Exception as e:
        log.warning("proxy request failed path=%s: %s", path, e)
        return jsonify({"error": "upstream request failed"}), 502
    return Response(resp.content, status=resp.status_code,
                    content_type=resp.headers.get("Content-Type", "application/json"))

