"""User profile endpoints (about, trophies, posts, comments, overview) with archive fallback."""
from flask import Blueprint, jsonify, request
from media_detection import process_post, extract_posts, clean_url, DISABLE_NSFW
from reddit_client import reddit_get
from helpers import (CACHE_TTL_FEED, CACHE_TTL_SUBREDDIT, FEED_LIMIT, USERNAME_RE, POST_ID_RE,
                     add_time_param, listing_params, cached_json, json_or_error, server_cache, validate_params,
                     hydrate_linked_posts, log, UpstreamError)
from archive import (_normalize_comment, _fetch_archived_posts, _fetch_archived_comments,
                     _fetch_archived_overview, _arc_cursor)

bp = Blueprint("users", __name__)


@bp.route("/api/posts/live-info")
@server_cache(30)
def get_posts_live_info():
    """Current score/comment counts, to refresh archived cards whose snapshot is stale."""
    ids = [i for i in request.args.get("ids", "").split(",") if POST_ID_RE.match(i)][:100]
    if not ids:
        return jsonify({})
    try:
        resp = reddit_get(
            "https://www.reddit.com/api/info.json",
            params={"id": ",".join(f"t3_{i}" for i in ids), "raw_json": 1},
            timeout=8)
        if not resp.ok:
            return jsonify({})
        out = {}
        for c in resp.json().get("data", {}).get("children", []):
            d = c["data"]
            out[d["id"]] = {"score": d.get("score", 0), "num_comments": d.get("num_comments", 0)}
        return cached_json(out, 30)
    except Exception as e:
        log.warning("live-info fetch failed: %s", e)
        return jsonify({})


def fetch_user_about(username, timeout=10):
    resp = reddit_get(
        f"https://www.reddit.com/user/{username}/about.json",
        params={"raw_json": 1}, timeout=timeout)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code, "User not found")
    d    = resp.json()["data"]
    icon = clean_url(d.get("icon_img") or d.get("snoovatar_img") or "")
    sub  = d.get("subreddit") or {}
    return {
        "name":                d["name"],
        "icon":                icon or "",
        "description":         sub.get("public_description", "") or "",
        "karma_post":          d.get("link_karma", 0),
        "karma_comment":       d.get("comment_karma", 0),
        "karma_award":         d.get("awarder_karma", 0),
        "karma_total":         d.get("total_karma", d.get("link_karma", 0) + d.get("comment_karma", 0)),
        "created_utc":         d.get("created_utc", 0),
        "is_premium":          d.get("is_gold", False),
        "is_mod":              d.get("is_mod", False),
        "is_employee":         d.get("is_employee", False),
        "verified":            d.get("verified", False),
        "has_verified_email":  d.get("has_verified_email", False),
    }


@bp.route("/api/user/<username>/about")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_user_about(username):
    return json_or_error(lambda: fetch_user_about(username), CACHE_TTL_FEED)


@bp.route("/api/user/<username>/trophies")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_SUBREDDIT)
def get_user_trophies(username):
    try:
        resp = reddit_get(
            f"https://www.reddit.com/api/v1/user/{username}/trophies.json",
            params={"raw_json": 1}, timeout=10)
        if resp.status_code != 200:
            return jsonify({"trophies": []})
        trophies = resp.json().get("data", {}).get("trophies", [])
        out = []
        for t in trophies:
            td = t.get("data", {})
            if not td.get("name"):
                continue
            out.append({
                "name":        td.get("name", ""),
                "description": td.get("description") or "",
                "icon":        clean_url(td.get("icon_40") or td.get("icon_70") or ""),
                "granted_at":  td.get("granted_at") or 0,
            })
        return cached_json({"trophies": out}, CACHE_TTL_SUBREDDIT)
    except Exception as e:
        log.warning("get_user_trophies failed user=%s: %s", username, e)
        return jsonify({"trophies": []})


_PROFILE_GONE = "User not found or profile is private"


def _archived(items, hydrate):
    if hydrate:
        hydrate_linked_posts(items)
    return items, _arc_cursor(items, FEED_LIMIT)


def _try_archive(username, fetch_archived):
    try:
        return fetch_archived(FEED_LIMIT)
    except Exception as e:
        log.warning("archived fallback failed for %s: %s", username, e)
        return []


def _fetch_listing_with_archive(username, after, do_live_request, parse_live_items,
                                fetch_archived, hydrate=False):
    """Reddit's live listing, falling back to the Arctic Shift archive on 403/404 or an
    empty first page (suspended/deleted accounts). Returns (items, next_after, archived)."""
    if after.startswith("arc:"):
        return (*_archived(fetch_archived(FEED_LIMIT, before=int(after[4:])), hydrate), True)
    resp = do_live_request()
    if resp.status_code in (403, 404):
        items = _try_archive(username, fetch_archived)
        if items:
            return (*_archived(items, hydrate), True)
        raise UpstreamError(_PROFILE_GONE, 404)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    listing = resp.json()["data"]
    items   = parse_live_items(listing)
    if not items and not after:
        archived = _try_archive(username, fetch_archived)
        if archived:
            return (*_archived(archived, hydrate), True)
    if hydrate:
        hydrate_linked_posts(items)
    return items, listing.get("after"), False


def _listing_params(sort, t, after):
    params = {**listing_params(after=after), "sort": sort}
    add_time_param(params, sort, t, sorts_with_time=("top",))
    return params


def fetch_user_posts(username, sort='new', t='', after='', timeout=10):
    """A user's submissions: {"posts", "after"}, plus "archived": True when served
    from the Arctic Shift archive."""
    posts, next_after, archived = _fetch_listing_with_archive(
        username, after,
        do_live_request=lambda: reddit_get(f"https://www.reddit.com/user/{username}/submitted.json",
                                            params=_listing_params(sort, t, after), timeout=timeout),
        parse_live_items=extract_posts,
        fetch_archived=lambda limit, before=None: _fetch_archived_posts(username, limit, before=before),
        hydrate=True,
    )
    return {"posts": posts, "after": next_after, **({"archived": True} if archived else {})}


def _parse_comment_listing(listing):
    return [_normalize_comment(c["data"]) for c in listing["children"] if c.get("kind") == "t1"]


def fetch_user_comments(username, sort='new', t='', after='', timeout=10):
    """A user's comments: {"comments", "after"}, plus "archived" like fetch_user_posts."""
    comments, next_after, archived = _fetch_listing_with_archive(
        username, after,
        do_live_request=lambda: reddit_get(f"https://www.reddit.com/user/{username}/comments.json",
                                            params=_listing_params(sort, t, after), timeout=timeout),
        parse_live_items=_parse_comment_listing,
        fetch_archived=lambda limit, before=None: _fetch_archived_comments(username, limit, before=before),
    )
    return {"comments": comments, "after": next_after, **({"archived": True} if archived else {})}


def _user_listing_endpoint(fetch, username):
    return json_or_error(lambda: fetch(username, request.args.get("sort", "new"), request.args.get("t", ""),
                                       request.args.get("after", "")), CACHE_TTL_FEED)


@bp.route("/api/user/<username>/posts")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_user_posts_api(username):
    return _user_listing_endpoint(fetch_user_posts, username)


@bp.route("/api/user/<username>/comments")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_user_comments_api(username):
    return _user_listing_endpoint(fetch_user_comments, username)


def _archived_overview(username):
    try:
        items, next_after = _fetch_archived_overview(username)
    except Exception as e:
        log.warning("archived overview fallback failed for %s: %s", username, e)
        return None
    return {"items": items, "after": next_after, "archived": True} if items else None


def fetch_user_overview(username, sort='new', t='', after='', timeout=10, allow_archive=True):
    """A user's mixed feed: {"items": [{"type", "data"}], "after"}, plus "archived".
    allow_archive=False skips the slow archive fallback (for SSR, where the client
    fetches it later instead)."""
    if after.startswith("arc:"):
        items, next_after = _fetch_archived_overview(username, before=int(after[4:]))
        return {"items": items, "after": next_after, "archived": True}

    resp = reddit_get(
        f"https://www.reddit.com/user/{username}/overview.json",
        params=_listing_params(sort, t, after), timeout=timeout)
    if resp.status_code in (403, 404):
        archived = _archived_overview(username) if allow_archive else None
        if archived:
            return archived
        raise UpstreamError(_PROFILE_GONE, 404)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    listing = resp.json()["data"]
    items = []
    for child in listing["children"]:
        kind = child.get("kind")
        d    = child.get("data", {})
        if kind == "t3":
            try:
                post = process_post(d)
                if not (DISABLE_NSFW and post.get("over_18")):
                    items.append({"type": "post", "data": post})
            except Exception as e:
                log.warning("overview process_post failed id=%s: %s", d.get("id"), e)
        elif kind == "t1":
            items.append({"type": "comment", "data": _normalize_comment(d)})
    hydrate_linked_posts([i["data"] for i in items if i["type"] == "post"])
    if not items and not after and allow_archive:
        archived = _archived_overview(username)
        if archived:
            return archived
    return {"items": items, "after": listing.get("after")}


@bp.route("/api/user/<username>/overview")
@validate_params(username=USERNAME_RE)
@server_cache(CACHE_TTL_FEED)
def get_user_overview_api(username):
    return _user_listing_endpoint(fetch_user_overview, username)
