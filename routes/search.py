"""Post, community, and user search plus subreddit autocomplete."""
import requests
from flask import Blueprint, jsonify, request
from media_detection import extract_posts, clean_url
from reddit_client import reddit_get
from helpers import CACHE_TTL_FEED, FEED_LIMIT, cached_json, server_cache, hydrate_linked_posts, log

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
            params={"query": q, "include_over_18": "true", "include_profiles": "false", "limit": 8},
            timeout=5)
        if resp.status_code != 200:
            return jsonify({"subs": []})
        children = resp.json().get("data", {}).get("children", [])
        subs = [{
            "name":        c["data"].get("display_name", ""),
            "icon":        clean_url(c["data"].get("icon_img") or c["data"].get("community_icon") or ""),
            "subscribers": c["data"].get("subscribers", 0),
            "over18":      bool(c["data"].get("over18")),
        } for c in children if c.get("data", {}).get("display_name")]
        return jsonify({"subs": subs[:8]})
    except Exception as e:
        log.warning("subreddit_search failed q=%r: %s", q, e)
        return jsonify({"subs": []})


# ── Search API ───────────────────────────────────────────────────────────────

SEARCH_SORTS = {'relevance', 'hot', 'top', 'new'}

@bp.route("/api/search")
@server_cache(CACHE_TTL_FEED)
def search_posts():
    q     = request.args.get("q", "").strip()
    sort  = request.args.get("sort", "relevance")
    t     = request.args.get("t", "all")
    after = request.args.get("after", "")
    sub   = request.args.get("sub", "")
    if not q:
        return jsonify({"error": "Missing query"}), 400
    if sort not in SEARCH_SORTS:
        sort = "relevance"
    url    = f"https://www.reddit.com/r/{sub}/search.json" if sub else "https://www.reddit.com/search.json"
    nsfw   = request.args.get("nsfw", "0") == "1"
    params = {"q": q, "sort": sort, "t": t, "limit": FEED_LIMIT, "raw_json": 1, "include_over_18": int(nsfw)}
    if sub:
        params["restrict_sr"] = 1
    if after:
        params["after"] = after
    try:
        resp = reddit_get(url, params=params, timeout=10)
        if resp.status_code == 404:
            return jsonify({"error": "Not found"}), 404
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = extract_posts(listing)
        hydrate_linked_posts(posts)
        return cached_json({"posts": posts, "after": listing.get("after")}, CACHE_TTL_FEED)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/search/communities")
@server_cache(CACHE_TTL_FEED)
def search_communities():
    q = request.args.get("q", "").strip()
    after = request.args.get("after", "")
    if not q:
        return jsonify({"communities": [], "after": None})
    try:
        params = {"q": q, "limit": FEED_LIMIT, "raw_json": 1, "type": "sr"}
        if after:
            params["after"] = after
        resp = reddit_get("https://www.reddit.com/search.json",
                           params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"communities": [], "after": None})
        listing = resp.json()["data"]
        results = []
        for c in listing["children"]:
            if c.get("kind") != "t5":
                continue
            d = c["data"]
            icon = clean_url(d.get("icon_img") or d.get("community_icon") or "")
            results.append({
                "name":        d.get("display_name", ""),
                "title":       d.get("title", ""),
                "description": d.get("public_description", ""),
                "subscribers": d.get("subscribers", 0),
                "over_18":     d.get("over_18", False),
                "icon":        icon or "",
            })
        return jsonify({"communities": results, "after": listing.get("after")})
    except Exception as e:
        return jsonify({"communities": [], "after": None})


@bp.route("/api/search/users")
@server_cache(CACHE_TTL_FEED)
def search_users():
    q = request.args.get("q", "").strip()
    after = request.args.get("after", "")
    if not q:
        return jsonify({"users": [], "after": None})
    try:
        params = {"q": q, "limit": FEED_LIMIT, "raw_json": 1, "type": "user"}
        if after:
            params["after"] = after
        resp = reddit_get("https://www.reddit.com/search.json",
                           params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"users": [], "after": None})
        listing = resp.json()["data"]
        results = []
        for c in listing["children"]:
            if c.get("kind") != "t2":
                continue
            d = c["data"]
            icon = clean_url(d.get("icon_img") or d.get("snoovatar_img") or "")
            results.append({
                "name":          d.get("name", ""),
                "icon":          icon or "",
                "karma_post":    d.get("link_karma", 0),
                "karma_comment": d.get("comment_karma", 0),
                "created_utc":   d.get("created_utc", 0),
            })
        return jsonify({"users": results, "after": listing.get("after")})
    except Exception as e:
        return jsonify({"users": [], "after": None})
