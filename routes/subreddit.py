"""Subreddit feed, about/rules/moderators/widgets, duplicates, wiki, and multireddits."""
import re
import html as html_lib
import requests
from flask import Blueprint, jsonify, request
from media_detection import process_post, extract_posts, clean_url
from reddit_client import reddit_get
from helpers import (CACHE_TTL_FEED, CACHE_TTL_SUBREDDIT, FEED_LIMIT, FEED_SORTS,
                     SUBREDDIT_RE, USERNAME_RE, POST_ID_RE, MULTINAME_RE,
                     add_time_param, cached_json, server_cache, validate_params,
                     hydrate_linked_posts, log)

bp = Blueprint("subreddit", __name__)


_WIDGET_KINDS = {"community-list", "calendar", "image", "textarea", "button", "menu"}


@bp.route("/api/r/<subreddit>/widgets")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_widgets(subreddit):
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/api/widgets.json",
            params={"progressive_images": "true", "raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"widgets": []})
        d       = resp.json()
        items   = d.get("items", {})
        order   = (d.get("layout", {}).get("sidebar", {}) or {}).get("order", [])
        topbar  = (d.get("layout", {}).get("topbar", {}) or {}).get("order", [])
        widgets = []
        for wid in [*topbar, *order]:
            w = items.get(wid)
            if not w or w.get("kind") not in _WIDGET_KINDS:
                continue
            kind = w["kind"]
            entry = {"kind": kind, "name": w.get("shortName", "")}
            if kind == "community-list":
                entry["items"] = [{
                    "name": c.get("name", ""),
                    "icon": clean_url(c.get("communityIcon") or c.get("iconUrl") or ""),
                    "subscribers": c.get("subscribers", 0),
                    "over18": bool(c.get("isNSFW")),
                } for c in w.get("data", [])]
            elif kind == "calendar":
                entry["events"] = [{
                    "title": ev.get("title", ""),
                    "startTime": ev.get("startTime"),
                    "allDay": bool(ev.get("allDay")),
                } for ev in w.get("data", [])][:8]
            elif kind == "image":
                entry["images"] = [{
                    "url": clean_url(im.get("url") or ""),
                    "linkUrl": im.get("linkUrl") or "",
                } for im in w.get("data", []) if im.get("url")]
            elif kind == "textarea":
                entry["text"] = w.get("text", "")
            elif kind == "button":
                entry["buttons"] = [{
                    "text": b.get("text", ""),
                    "url": b.get("url", ""),
                    "color": b.get("color", ""),
                } for b in w.get("buttons", [])]
            elif kind == "menu":
                entry["links"] = [{
                    "text": m.get("text", ""),
                    "url": m.get("url", ""),
                } for m in w.get("data", []) if m.get("url")]
            content_key = {"community-list": "items", "calendar": "events", "image": "images",
                           "button": "buttons", "menu": "links"}.get(kind)
            if content_key is not None and not entry.get(content_key):
                continue
            if kind == "textarea" and not entry.get("text", "").strip():
                continue
            widgets.append(entry)
        return cached_json({"widgets": widgets}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        log.warning("get_widgets failed sub=%s: %s", subreddit, e)
        return jsonify({"widgets": []})


# Reddit's error responses for non-200 subreddit requests carry a "reason" field
# identifying why access was blocked, distinct from a plain "doesn't exist" 404.
_SUBREDDIT_ERROR_MESSAGES = {
    "banned":      "This subreddit has been banned",
    "private":     "This subreddit is private",
    "quarantined": "This subreddit is quarantined",
    "gated":       "This subreddit requires content-warning acknowledgement",
}

def _subreddit_error_state(resp):
    """Classify a non-200 subreddit response. Returns (state, message)."""
    try:
        body = resp.json() or {}
    except Exception:
        body = {}
    reason = body.get("reason")
    if reason in _SUBREDDIT_ERROR_MESSAGES:
        message = (body.get("quarantine_message") or body.get("interstitial_warning_message")
                   or _SUBREDDIT_ERROR_MESSAGES[reason])
        return reason, message
    if resp.status_code == 404:
        return "not_found", "Subreddit not found"
    if resp.status_code == 403:
        return "private", "Subreddit is private"
    return "error", f"Reddit returned {resp.status_code}"


def _subreddit_error_response(resp):
    """Classify a non-200 subreddit response into a jsonify (body, status) tuple."""
    state, message = _subreddit_error_state(resp)
    status = 404 if state in ("banned", "not_found") else 403 if state != "error" else resp.status_code
    return jsonify({"error": message, "state": state}), status


def _quarantine_fallback_posts(subreddit, after=None, target=FEED_LIMIT):
    """Quarantined subreddit listings are blocked for anonymous sessions even after
    opt-in. Fall back: paginate the comments feed (accessible) to collect post IDs,
    then batch-fetch those posts via /by_id/."""
    from reddit_client import _get_quarantine_session
    s = _get_quarantine_session()
    try:
        seen, ids, cursor = set(), [], after
        for _ in range(4):          # up to 4 pages of comments (400 comments max)
            params = {"limit": 100, "raw_json": 1}
            if cursor:
                params["after"] = cursor
            rc = s.get(
                f"https://www.reddit.com/r/{subreddit}/comments.json",
                params=params,
                timeout=10,
            )
            if not rc.ok:
                break
            data = rc.json().get("data", {})
            for c in data.get("children", []):
                lid = c.get("data", {}).get("link_id", "")
                if lid and lid not in seen:
                    seen.add(lid)
                    ids.append(lid)
            cursor = data.get("after")
            if not cursor or len(ids) >= target:
                break
        if not ids:
            return [], None
        rb = s.get(
            f"https://www.reddit.com/by_id/{','.join(ids[:target])}.json",
            params={"raw_json": 1},
            timeout=10,
        )
        if not rb.ok:
            return [], None
        return extract_posts(rb.json()["data"]), cursor
    except Exception as e:
        return [], None


@bp.route("/api/r/<subreddit>")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_FEED)
def get_posts(subreddit):
    sort  = request.args.get("sort", "top")
    if sort not in FEED_SORTS:
        sort = "top"
    t     = request.args.get("t", "")
    after             = request.args.get("after", "")
    quarantine_opt_in = request.args.get("quarantine_opt_in", "")
    url   = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    params = {"limit": FEED_LIMIT, "raw_json": 1}
    add_time_param(params, sort, t)
    if after:
        params["after"] = after
    try:
        resp = reddit_get(url, quarantine=bool(quarantine_opt_in), params=params, timeout=10)
        if resp.status_code != 200:
            return _subreddit_error_response(resp)
        listing = resp.json()["data"]
        posts   = extract_posts(listing)
        fallback_after = None
        if not posts and quarantine_opt_in:
            posts, fallback_after = _quarantine_fallback_posts(subreddit, after or None)
        hydrate_linked_posts(posts)
        return cached_json({"posts": posts, "after": fallback_after or listing.get("after")}, CACHE_TTL_FEED)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/r/<subreddit>/about")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_FEED)
def get_about(subreddit):
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/about.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return _subreddit_error_response(resp)
        d      = resp.json()["data"]
        icon   = clean_url(d.get("icon_img") or d.get("community_icon") or "")
        active = d.get("active_user_count") or d.get("accounts_active") or 0
        sub_type = d.get("subreddit_type", "public")
        state = "quarantined" if d.get("quarantine") else (sub_type if sub_type != "public" else None)
        return cached_json({
            "title":       d.get("title", subreddit),
            "description": d.get("public_description", ""),
            "sidebar":     d.get("description", ""),
            "subscribers": d.get("subscribers", 0),
            "active":      active,
            "icon":        icon or "",
            "state":       state,
        }, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/r/<subreddit>/rules")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_rules(subreddit):
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/about/rules.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"rules": []})
        rules = resp.json().get("rules", [])
        return cached_json({"rules": [{"short_name": r.get("short_name",""), "description": r.get("description","")} for r in rules]}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        log.warning("get_rules failed sub=%s: %s", subreddit, e)
        return jsonify({"rules": []})


@bp.route("/api/r/<subreddit>/about/moderators")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_moderators(subreddit):
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/about/moderators.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"moderators": []})
        children = resp.json().get("data", {}).get("children", [])
        mods = [{"name": m.get("name", "")} for m in children if m.get("name")]
        return cached_json({"moderators": mods}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        log.warning("get_moderators failed sub=%s: %s", subreddit, e)
        return jsonify({"moderators": []})


@bp.route("/api/r/<subreddit>/duplicates/<post_id>")
@validate_params(subreddit=SUBREDDIT_RE, post_id=POST_ID_RE)
@server_cache(CACHE_TTL_FEED)
def get_duplicates(subreddit, post_id):
    try:
        after = request.args.get("after", "")
        params = {"raw_json": 1, "limit": 25}
        if after:
            params["after"] = after
        resp = reddit_get(
            f"https://old.reddit.com/r/{subreddit}/duplicates/{post_id}.json",
            params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        data = resp.json()
        orig_children = data[0]["data"]["children"]
        post = process_post(orig_children[0]["data"]) if orig_children else None
        if post:
            post["selftext"] = orig_children[0]["data"].get("selftext", "")
        listing = data[1]["data"]
        posts = extract_posts(listing)
        hydrate_linked_posts(([post] if post else []) + posts)
        return cached_json({"post": post, "posts": posts, "after": listing.get("after")}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


WIKI_PAGE_RE = re.compile(r'^[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*$')

@bp.route("/api/r/<subreddit>/wiki")
@bp.route("/api/r/<subreddit>/wiki/<path:page>")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_wiki(subreddit, page='index'):
    if not WIKI_PAGE_RE.match(page):
        return jsonify({"error": "Invalid page name"}), 400
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/wiki/{page}.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "Wiki page not found"}), 404
        if resp.status_code == 403:
            return jsonify({"error": "Wiki is private or disabled"}), 403
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        d = resp.json()["data"]
        raw_html = html_lib.unescape(d.get("content_html", ""))
        raw_html = re.sub(r'<!--\s*SC_(?:OFF|ON)\s*-->', '', raw_html).strip()
        return cached_json({
            "content_html":   raw_html,
            "revision_date":  d.get("revision_date"),
        }, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/user/<username>/m/<multiname>")
@validate_params(username=USERNAME_RE, multiname=MULTINAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_multireddit(username, multiname):
    sort  = request.args.get("sort", "hot")
    t     = request.args.get("t", "")
    after = request.args.get("after", "")
    params = {"limit": FEED_LIMIT, "raw_json": 1}
    add_time_param(params, sort, t)
    if after:
        params["after"] = after
    try:
        meta = reddit_get(
            f"https://www.reddit.com/api/multi/user/{username}/m/{multiname}.json",
            params={"raw_json": 1}, timeout=10)
        if meta.status_code == 404:
            return jsonify({"error": "Multireddit not found"}), 404
        if meta.status_code != 200:
            return jsonify({"error": f"Reddit returned {meta.status_code}"}), meta.status_code
        meta_data = meta.json().get("data", {})
        subs = [s["name"] for s in meta_data.get("subreddits", [])]
        if not subs:
            return cached_json({"posts": [], "after": None, "title": multiname}, CACHE_TTL_FEED)
        combined = "+".join(subs[:100])
        resp = reddit_get(
            f"https://www.reddit.com/r/{combined}/{sort}.json",
            params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        display = meta_data.get("display_name") or meta_data.get("name") or multiname
        posts   = extract_posts(listing)
        hydrate_linked_posts(posts)
        return cached_json({"posts": posts, "after": listing.get("after"), "title": display}, CACHE_TTL_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
