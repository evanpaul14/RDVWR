"""Search from the Reddit Android app's GraphQL endpoint, for when Reddit's WAF blocks the REST
search (see gql_posts.py). Results are the dicts routes/search.py builds.

The anonymous device identity the app uses never sees NSFW results, whatever the filters, so
this fallback searches SFW content only."""
from gql_client import gql_query
from gql_posts import POSTS_FLAGS, posts_from_nodes
from helpers import FEED_LIMIT, TIME_FILTERS
from media_detection import clean_url
from datetime import datetime

SURFACE = "android"   # SearchPosts / SearchPeople's required productSurface


def _page(connection):
    """(nodes, next cursor) of a GraphQL connection."""
    info = connection.get("pageInfo") or {}
    nodes = [e["node"] for e in connection.get("edges") or [] if e.get("node")]
    return nodes, (info.get("endCursor") or None) if info.get("hasNextPage") else None


def fetch_gql_search_posts(q, sort="relevance", t="all", sub="", after="", timeout=15):
    """{"posts", "after"} for a post search, optionally restricted to one subreddit."""
    variables = {"query": f"subreddit:{sub} {q}" if sub else q, "productSurface": SURFACE,
                 "pageSize": FEED_LIMIT, "sort": sort.upper(), **POSTS_FLAGS}
    if t in TIME_FILTERS:
        variables["filters"] = [{"key": "time_range", "value": t}]
    if after:
        variables["afterCursor"] = after
    data = gql_query("SearchPosts", variables, timeout)
    nodes, cursor = _page((((data.get("data") or {}).get("search") or {}).get("general") or {}).get("posts") or {})
    return {"posts": posts_from_nodes(nodes), "after": cursor}


def fetch_gql_search_users(q, after="", timeout=15):
    """{"users", "after"}. The app reports one total karma, not post / comment karma, so the
    users carry `karma_total` and zeroes for the split (the UI prints the total when present)."""
    variables = {"query": q, "productSurface": SURFACE, "pageSize": FEED_LIMIT}
    if after:
        variables["afterCursor"] = after
    data = gql_query("SearchPeople", variables, timeout)
    nodes, cursor = _page((((data.get("data") or {}).get("search") or {}).get("general") or {}).get("authors") or {})
    users = []
    for n in nodes:
        profile = n.get("profile") or {}
        try:
            created = datetime.fromisoformat(profile.get("createdAt")).timestamp()
        except (TypeError, ValueError):
            created = 0
        users.append({
            "name":          n.get("name", ""),
            "icon":          clean_url(((profile.get("styles") or {}).get("legacyIcon") or {}).get("url") or "") or "",
            "karma_post":    0,
            "karma_comment": 0,
            "karma_total":   (n.get("karma") or {}).get("total") or 0,
            "created_utc":   created,
        })
    return {"users": users, "after": cursor}


def fetch_gql_search_communities(q, timeout=15):
    """{"communities", "after"}: name matches only (the app's community lookup has no title,
    NSFW flag or paging)."""
    data = gql_query("CommunitySearch", {"query": q}, timeout)
    communities = []
    for c in (data.get("data") or {}).get("targetableCommunityInfo") or []:
        name = (c.get("subredditInfo") or {}).get("name")
        if name:
            communities.append({"name": name, "title": name, "description": c.get("description") or "",
                                "subscribers": int(c.get("subscriberCount") or 0), "over_18": False,
                                "icon": clean_url(c.get("iconUrl") or "") or ""})
    return {"communities": communities, "after": None}
