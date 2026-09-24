"""Subreddit feed, about/rules/moderators/widgets, duplicates, wiki, and multireddits."""
import re
import html as html_lib
from flask import Blueprint, request
from media_detection import process_post, extract_posts, clean_url
from reddit_html import clean_reddit_html, SC_MARKER_RE
from reddit_client import reddit_get, get_quarantine_session
from helpers import (CACHE_TTL_FEED, CACHE_TTL_SUBREDDIT, FEED_LIMIT, FEED_SORTS,
                     SUBREDDIT_RE, USERNAME_RE, POST_ID_RE, MULTINAME_RE,
                     listing_params, cached_json, json_or_error, server_cache, validate_params,
                     hydrate_linked_posts, log, UpstreamError)

bp = Blueprint("subreddit", __name__)


_WIDGET_KINDS = {"community-list", "calendar", "image", "textarea", "button", "menu"}
# Widget kinds dropped when this list comes out empty.
_WIDGET_CONTENT_KEYS = {"community-list": "items", "calendar": "events", "image": "images",
                        "button": "buttons", "menu": "links"}


def fetch_widgets(subreddit, timeout=10):
    """A subreddit's sidebar widgets, in display order. Best-effort: [] on any failure."""
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/api/widgets.json",
            params={"progressive_images": "true", "raw_json": 1}, timeout=timeout)
        if resp.status_code != 200:
            return []
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
                entry["text_html"] = clean_reddit_html(w.get("textHtml"))
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
            content_key = _WIDGET_CONTENT_KEYS.get(kind)
            if content_key is not None and not entry.get(content_key):
                continue
            if kind == "textarea" and not entry.get("text", "").strip():
                continue
            widgets.append(entry)
        return widgets
    except Exception as e:
        log.warning("fetch_widgets failed sub=%s: %s", subreddit, e)
        return []


@bp.route("/api/r/<subreddit>/widgets")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_widgets(subreddit):
    return cached_json({"widgets": fetch_widgets(subreddit)}, CACHE_TTL_SUBREDDIT)


# Keyed by the "reason" field of Reddit's error body.
_SUBREDDIT_ERROR_MESSAGES = {
    "banned":      "This subreddit has been banned",
    "private":     "This subreddit is private",
    "quarantined": "This subreddit is quarantined",
    "gated":       "This subreddit requires content-warning acknowledgement",
}


def _subreddit_error_state(resp):
    """(state, message) for a non-200 subreddit response."""
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


def _subreddit_error(resp):
    state, message = _subreddit_error_state(resp)
    status = 404 if state in ("banned", "not_found") else 403 if state != "error" else resp.status_code
    return UpstreamError(message, status, state)


def _quarantine_fallback_posts(subreddit, after=None, target=FEED_LIMIT):
    """Quarantined listings stay blocked even after opt-in, but the comments feed
    doesn't: collect post IDs from it, then batch-fetch those posts."""
    s = get_quarantine_session()
    try:
        seen, ids, cursor = set(), [], after
        for _ in range(4):
            rc = s.get(f"https://www.reddit.com/r/{subreddit}/comments.json",
                       params=listing_params(after=cursor, limit=100), timeout=10)
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
        rb = s.get(f"https://www.reddit.com/by_id/{','.join(ids[:target])}.json",
                   params={"raw_json": 1}, timeout=10)
        if not rb.ok:
            return [], None
        return extract_posts(rb.json()["data"]), cursor
    except Exception as e:
        log.warning("quarantine fallback failed sub=%s: %s", subreddit, e)
        return [], None


def _listing_url(subreddits, sort):
    # oauth.reddit.com needs "+" percent-encoded, or it redirects to the HTML front page.
    return f"https://www.reddit.com/r/{subreddits.replace('+', '%2B')}/{sort}.json"


def fetch_feed(subreddit, sort, t='', after='', quarantine_opt_in=False, timeout=10):
    """A subreddit (or a+b combined) listing: {"posts", "after"}. Raises UpstreamError
    with .state set when Reddit refuses it."""
    resp = reddit_get(_listing_url(subreddit, sort), quarantine=bool(quarantine_opt_in),
                      params=listing_params(after, sort=sort, t=t), timeout=timeout)
    if resp.status_code != 200:
        if quarantine_opt_in and _subreddit_error_state(resp)[0] == "quarantined":
            posts, fallback_after = _quarantine_fallback_posts(subreddit, after or None)
            if posts:
                hydrate_linked_posts(posts)
                return {"posts": posts, "after": fallback_after}
        raise _subreddit_error(resp)
    listing = resp.json()["data"]
    posts   = extract_posts(listing)
    next_after = listing.get("after")
    if not posts and quarantine_opt_in:
        posts, next_after = _quarantine_fallback_posts(subreddit, after or None)
    hydrate_linked_posts(posts)
    return {"posts": posts, "after": next_after}


@bp.route("/api/r/<subreddit>")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_FEED)
def get_posts(subreddit):
    sort = request.args.get("sort", "top")
    if sort not in FEED_SORTS:
        sort = "top"
    return json_or_error(lambda: fetch_feed(subreddit, sort, request.args.get("t", ""),
                                            request.args.get("after", ""),
                                            bool(request.args.get("quarantine_opt_in", ""))), CACHE_TTL_FEED)


def fetch_about(subreddit, timeout=10):
    """Header info: `sidebar` is markdown for JS, `sidebar_html` sanitized HTML for noscript."""
    resp = reddit_get(
        f"https://www.reddit.com/r/{subreddit}/about.json",
        params={"raw_json": 1}, timeout=timeout)
    if resp.status_code != 200:
        raise _subreddit_error(resp)
    d      = resp.json()["data"]
    icon   = clean_url(d.get("icon_img") or d.get("community_icon") or "")
    active = d.get("active_user_count") or d.get("accounts_active") or 0
    sub_type = d.get("subreddit_type", "public")
    state = "quarantined" if d.get("quarantine") else (sub_type if sub_type != "public" else None)
    return {
        "title":        d.get("title", subreddit),
        "description":  d.get("public_description", ""),
        "sidebar":      d.get("description", ""),
        "sidebar_html": clean_reddit_html(d.get("description_html")),
        "subscribers":  d.get("subscribers", 0),
        "active":       active,
        "icon":         icon or "",
        "state":        state,
    }


@bp.route("/api/r/<subreddit>/about")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_FEED)
def get_about(subreddit):
    return json_or_error(lambda: fetch_about(subreddit), CACHE_TTL_FEED)


def fetch_rules(subreddit, timeout=10):
    """Best-effort: [] on any failure."""
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/about/rules.json",
            params={"raw_json": 1}, timeout=timeout)
        if resp.status_code != 200:
            return []
        return [{"short_name": r.get("short_name", ""), "description": r.get("description", "")}
                for r in resp.json().get("rules", [])]
    except Exception as e:
        log.warning("fetch_rules failed sub=%s: %s", subreddit, e)
        return []


@bp.route("/api/r/<subreddit>/rules")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_rules(subreddit):
    return cached_json({"rules": fetch_rules(subreddit)}, CACHE_TTL_SUBREDDIT)


def fetch_moderators(subreddit, timeout=10):
    """Best-effort: [] on any failure."""
    try:
        resp = reddit_get(
            f"https://www.reddit.com/r/{subreddit}/about/moderators.json",
            params={"raw_json": 1}, timeout=timeout)
        if resp.status_code != 200:
            return []
        children = resp.json().get("data", {}).get("children", [])
        return [{"name": m.get("name", "")} for m in children if m.get("name")]
    except Exception as e:
        log.warning("fetch_moderators failed sub=%s: %s", subreddit, e)
        return []


@bp.route("/api/r/<subreddit>/about/moderators")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_moderators(subreddit):
    return cached_json({"moderators": fetch_moderators(subreddit)}, CACHE_TTL_SUBREDDIT)


def fetch_duplicates(subreddit, post_id, after='', timeout=10):
    """Other posts linking to the same URL: {"post" (the original), "posts", "after"}."""
    resp = reddit_get(
        f"https://old.reddit.com/r/{subreddit}/duplicates/{post_id}.json",
        params=listing_params(after=after, limit=25), timeout=timeout)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code, "Post not found")
    data = resp.json()
    orig_children = data[0]["data"]["children"]
    post = process_post(orig_children[0]["data"]) if orig_children else None
    if post:
        post["selftext"] = orig_children[0]["data"].get("selftext", "")
    listing = data[1]["data"]
    posts = extract_posts(listing)
    hydrate_linked_posts(([post] if post else []) + posts)
    return {"post": post, "posts": posts, "after": listing.get("after")}


@bp.route("/api/r/<subreddit>/duplicates/<post_id>")
@validate_params(subreddit=SUBREDDIT_RE, post_id=POST_ID_RE)
@server_cache(CACHE_TTL_FEED)
def get_duplicates(subreddit, post_id):
    return json_or_error(lambda: fetch_duplicates(subreddit, post_id, request.args.get("after", "")),
                         CACHE_TTL_FEED)


WIKI_PAGE_RE = re.compile(r'^[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*$')


def fetch_wiki(subreddit, page='index', timeout=10):
    """A wiki page. `content_html` is unsanitized: callers must sanitize it (DOMPurify /
    reddit_html.sanitize_reddit_html)."""
    if not WIKI_PAGE_RE.match(page):
        raise UpstreamError("Invalid page name", 400)
    resp = reddit_get(
        f"https://www.reddit.com/r/{subreddit}/wiki/{page}.json",
        params={"raw_json": 1}, timeout=timeout)
    if resp.status_code == 403:
        raise UpstreamError("Wiki is private or disabled", 403)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code, "Wiki page not found")
    d = resp.json()["data"]
    return {
        "content_html":  SC_MARKER_RE.sub('', html_lib.unescape(d.get("content_html", ""))).strip(),
        "revision_date": d.get("revision_date"),
    }


@bp.route("/api/r/<subreddit>/wiki")
@bp.route("/api/r/<subreddit>/wiki/<path:page>")
@validate_params(subreddit=SUBREDDIT_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_wiki(subreddit, page='index'):
    return json_or_error(lambda: fetch_wiki(subreddit, page), CACHE_TTL_SUBREDDIT)


def fetch_multireddit(username, multiname, sort, t='', after='', timeout=10):
    """A user multireddit's combined listing: {"posts", "after", "title"}."""
    meta = reddit_get(
        f"https://www.reddit.com/api/multi/user/{username}/m/{multiname}.json",
        params={"raw_json": 1}, timeout=timeout)
    if meta.status_code != 200:
        raise UpstreamError.from_status(meta.status_code, "Multireddit not found")
    meta_data = meta.json().get("data", {})
    subs = [s["name"] for s in meta_data.get("subreddits", [])]
    display = meta_data.get("display_name") or meta_data.get("name") or multiname
    if not subs:
        return {"posts": [], "after": None, "title": display}
    resp = reddit_get(_listing_url("+".join(subs[:100]), sort),
                      params=listing_params(after, sort=sort, t=t), timeout=timeout)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    listing = resp.json()["data"]
    posts   = extract_posts(listing)
    hydrate_linked_posts(posts)
    return {"posts": posts, "after": listing.get("after"), "title": display}


@bp.route("/api/user/<username>/m/<multiname>")
@validate_params(username=USERNAME_RE, multiname=MULTINAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_multireddit(username, multiname):
    sort = request.args.get("sort", "hot")
    if sort not in FEED_SORTS:
        sort = "hot"
    return json_or_error(lambda: fetch_multireddit(username, multiname, sort, request.args.get("t", ""),
                                                   request.args.get("after", "")), CACHE_TTL_FEED)
