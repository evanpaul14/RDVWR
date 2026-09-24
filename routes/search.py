"""Post, community, and user search plus subreddit autocomplete."""
from flask import Blueprint, jsonify, request
from media_detection import extract_posts, clean_url, DISABLE_NSFW
from reddit_client import reddit_get
from helpers import (CACHE_TTL_FEED, SEARCH_SORTS, listing_params, json_or_error, server_cache,
                     hydrate_linked_posts, log, UpstreamError)

bp = Blueprint("search", __name__)


# ── Subreddit autocomplete ────────────────────────────────────────────────────

@bp.route("/api/subreddit-search")
def subreddit_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"subs": []})
    try:
        resp = reddit_get(
            "https://www.reddit.com/api/subreddit_autocomplete_v2.json",
            params={"query": q, "include_over_18": str(not DISABLE_NSFW).lower(),
                    "include_profiles": "false", "limit": 8},
            timeout=5)
        if resp.status_code != 200:
            return jsonify({"subs": []})
        children = resp.json().get("data", {}).get("children", [])
        subs = [{
            "name":        c["data"].get("display_name", ""),
            "icon":        clean_url(c["data"].get("icon_img") or c["data"].get("community_icon") or ""),
            "subscribers": c["data"].get("subscribers", 0),
            "over18":      bool(c["data"].get("over18")),
        } for c in children
            if c.get("data", {}).get("display_name")
            and not (DISABLE_NSFW and c["data"].get("over18"))]
        return jsonify({"subs": subs[:8]})
    except Exception as e:
        log.warning("subreddit_search failed q=%r: %s", q, e)
        return jsonify({"subs": []})


# ── Search API ───────────────────────────────────────────────────────────────

def fetch_search_posts(q, sort='relevance', t='all', sub='', nsfw=False, after='', timeout=10):
    """Post search, optionally restricted to one subreddit: {"posts", "after"}."""
    if sort not in SEARCH_SORTS:
        sort = "relevance"
    url    = f"https://www.reddit.com/r/{sub}/search.json" if sub else "https://www.reddit.com/search.json"
    params = {"q": q, "sort": sort, "t": t, **listing_params(after=after),
              "include_over_18": int(nsfw and not DISABLE_NSFW)}
    if sub:
        params["restrict_sr"] = 1
    resp = reddit_get(url, params=params, timeout=timeout)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    listing = resp.json()["data"]
    posts   = extract_posts(listing)
    hydrate_linked_posts(posts)
    return {"posts": posts, "after": listing.get("after")}


@bp.route("/api/search")
@server_cache(CACHE_TTL_FEED)
def search_posts():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Missing query"}), 400
    return json_or_error(lambda: fetch_search_posts(
        q, request.args.get("sort", "relevance"), request.args.get("t", "all"),
        request.args.get("sub", ""), request.args.get("nsfw", "0") == "1",
        request.args.get("after", "")), CACHE_TTL_FEED)


def _search_things(q, kind, after, timeout):
    """(raw `data` dicts of one kind — t5 subreddits / t2 users, next cursor)."""
    params = {**listing_params(after), "q": q, "type": {"t5": "sr", "t2": "user"}[kind]}
    resp = reddit_get("https://www.reddit.com/search.json", params=params, timeout=timeout)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    listing = resp.json()["data"]
    return [c["data"] for c in listing["children"] if c.get("kind") == kind], listing.get("after")


def fetch_search_communities(q, after='', timeout=10):
    things, next_after = _search_things(q, "t5", after, timeout)
    results = [{
        "name":        d.get("display_name", ""),
        "title":       d.get("title", ""),
        "description": d.get("public_description", ""),
        "subscribers": d.get("subscribers", 0),
        "over_18":     d.get("over_18", False),
        "icon":        clean_url(d.get("icon_img") or d.get("community_icon") or "") or "",
    } for d in things if not (DISABLE_NSFW and d.get("over_18"))]
    return {"communities": results, "after": next_after}


def fetch_search_users(q, after='', timeout=10):
    things, next_after = _search_things(q, "t2", after, timeout)
    results = [{
        "name":          d.get("name", ""),
        "icon":          clean_url(d.get("icon_img") or d.get("snoovatar_img") or "") or "",
        "karma_post":    d.get("link_karma", 0),
        "karma_comment": d.get("comment_karma", 0),
        "created_utc":   d.get("created_utc", 0),
    } for d in things]
    return {"users": results, "after": next_after}


def _best_effort_search(fetch, key):
    """Empty result instead of an error, so the search page shows "none found"."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({key: [], "after": None})
    try:
        return jsonify(fetch(q, request.args.get("after", "")))
    except Exception as e:
        log.warning("%s failed q=%r: %s", fetch.__name__, q, e)
        return jsonify({key: [], "after": None})


@bp.route("/api/search/communities")
@server_cache(CACHE_TTL_FEED)
def search_communities():
    return _best_effort_search(fetch_search_communities, "communities")


@bp.route("/api/search/users")
@server_cache(CACHE_TTL_FEED)
def search_users():
    return _best_effort_search(fetch_search_users, "users")
