"""Reddit live threads."""
import re
from flask import Blueprint, jsonify, request
from reddit_client import reddit_get
from reddit_html import clean_reddit_html
from helpers import json_or_error, parallel, log, UpstreamError

bp = Blueprint("live", __name__)


LIVE_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def _parse_live_updates(children):
    out = []
    for c in children:
        if c.get("kind") != "LiveUpdate":
            continue
        d = c["data"]
        out.append({
            "id":          d.get("id", ""),
            "body":        d.get("body", ""),
            "body_html":   clean_reddit_html(d.get("body_html")),
            "author":      d.get("author", "[deleted]"),
            "created_utc": d.get("created_utc", 0),
            "stricken":    d.get("stricken", False),
        })
    return out


def fetch_live_updates(thread_id, before='', after='', timeout=10):
    """One page of updates, newest first: {"updates", "after"}. Uses the thread's own
    .json, since /updates.json 404s over OAuth."""
    params = {"raw_json": 1, "limit": 25}
    if before:
        params["before"] = before
    if after:
        params["after"] = after
    resp = reddit_get(f"https://www.reddit.com/live/{thread_id}.json", params=params, timeout=timeout)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    listing = resp.json()["data"]
    return {"updates": _parse_live_updates(listing.get("children", [])), "after": listing.get("after")}


def fetch_live_thread(thread_id, after='', timeout=10):
    """Header info plus one page of updates (empty if that fetch fails)."""
    def _fetch_info():
        return reddit_get(f"https://www.reddit.com/live/{thread_id}/about.json", params={"raw_json": 1}, timeout=timeout)
    def _fetch_updates():
        try:
            return fetch_live_updates(thread_id, after=after, timeout=timeout)
        except Exception as e:
            log.warning("live updates fetch failed thread=%s: %s", thread_id, e)
            return {"updates": [], "after": None}
    info_resp, updates = parallel(_fetch_info, _fetch_updates)
    if info_resp.status_code != 200:
        raise UpstreamError.from_status(info_resp.status_code, "Live thread not found")
    d = info_resp.json()["data"]
    return {
        "title":            d.get("title", ""),
        "description":      d.get("description", ""),
        "description_html": clean_reddit_html(d.get("description_html")),
        "state":            d.get("state", "complete"),
        "viewer_count":     d.get("viewer_count", 0),
        **updates,
    }


@bp.route("/api/live/<thread_id>")
def get_live_thread(thread_id):
    if not LIVE_ID_RE.match(thread_id):
        return jsonify({"error": "Invalid thread ID"}), 400
    return json_or_error(lambda: fetch_live_thread(thread_id), 30)


@bp.route("/api/live/<thread_id>/updates")
def get_live_updates(thread_id):
    if not LIVE_ID_RE.match(thread_id):
        return jsonify({"error": "Invalid thread ID"}), 400
    return json_or_error(lambda: fetch_live_updates(thread_id, request.args.get("before", ""),
                                                    request.args.get("after", "")), 15)
