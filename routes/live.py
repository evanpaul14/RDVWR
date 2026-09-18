"""Reddit live threads."""
import re
from flask import Blueprint, jsonify, request
from reddit_client import reddit_get
from helpers import cached_json, error_response, parallel

bp = Blueprint("live", __name__)


LIVE_ID_RE          = re.compile(r'^[A-Za-z0-9_-]+$')


def _parse_live_updates(children):
    out = []
    for c in children:
        if c.get("kind") != "LiveUpdate":
            continue
        d = c["data"]
        out.append({
            "id":          d.get("id", ""),
            "body":        d.get("body", ""),
            "author":      d.get("author", "[deleted]"),
            "created_utc": d.get("created_utc", 0),
            "stricken":    d.get("stricken", False),
        })
    return out


@bp.route("/api/live/<thread_id>")
def get_live_thread(thread_id):
    if not LIVE_ID_RE.match(thread_id):
        return jsonify({"error": "Invalid thread ID"}), 400
    try:
        def _fetch_info():
            return reddit_get(f"https://www.reddit.com/live/{thread_id}.json", params={"raw_json": 1}, timeout=10)
        def _fetch_updates():
            return reddit_get(f"https://www.reddit.com/live/{thread_id}/updates.json", params={"raw_json": 1, "limit": 25}, timeout=10)
        info_resp, upd_resp = parallel(_fetch_info, _fetch_updates)
        if info_resp.status_code == 404:
            return jsonify({"error": "Live thread not found"}), 404
        if info_resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {info_resp.status_code}"}), info_resp.status_code
        d = info_resp.json()["data"]
        updates, after = [], None
        if upd_resp.status_code == 200:
            listing = upd_resp.json()["data"]
            updates = _parse_live_updates(listing.get("children", []))
            after   = listing.get("after")
        return cached_json({
            "title":        d.get("title", ""),
            "description":  d.get("description", ""),
            "state":        d.get("state", "complete"),
            "viewer_count": d.get("viewer_count", 0),
            "updates":      updates,
            "after":        after,
        }, 30)
    except Exception:
        return error_response(500)


@bp.route("/api/live/<thread_id>/updates")
def get_live_updates(thread_id):
    if not LIVE_ID_RE.match(thread_id):
        return jsonify({"error": "Invalid thread ID"}), 400
    before = request.args.get("before", "")
    after  = request.args.get("after",  "")
    try:
        params = {"raw_json": 1, "limit": 25}
        if before: params["before"] = before
        if after:  params["after"]  = after
        resp = reddit_get(
            f"https://www.reddit.com/live/{thread_id}/updates.json",
            params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        return cached_json({
            "updates": _parse_live_updates(listing.get("children", [])),
            "after":   listing.get("after"),
        }, 15)
    except Exception:
        return error_response(500)
