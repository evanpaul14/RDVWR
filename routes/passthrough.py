"""Reddit-style ".json" URLs (e.g. /r/pics/hot.json), proxied straight to Reddit's API."""
import re
from flask import Blueprint, Response, jsonify, request
from reddit_client import reddit_get
from helpers import log

bp = Blueprint("passthrough", __name__)

# Only public content paths, so the pooled OAuth tokens can't be used as an open proxy.
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
    """Runs before routing so ".json" URLs never hit the HTML page routes."""
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

