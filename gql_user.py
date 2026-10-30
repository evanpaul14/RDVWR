"""A user's comments and overview from the Reddit Android app's GraphQL endpoint, for when
Reddit's WAF blocks the REST listings (see gql_posts.py). Results are the dicts
routes/users.py builds.

The app only sends the first 300 characters of each comment in a listing, so longer
comments end in an ellipsis here (the full text is one click away on the post page)."""
import base64
import html as html_lib
import json
from datetime import datetime
from gql_client import gql_query
from gql_posts import fetch_gql_user_posts, from_gql_cursor, to_gql_cursor, _selftext_html
from helpers import FEED_LIMIT, UpstreamError, parallel
from reddit_html import clean_reddit_html

GONE = "User not found or profile is private"
PREVIEW_LIMIT = 300
SORTS = {"new", "top", "hot", "controversial"}
OVERVIEW_PREFIX = "gql2:"   # a composite cursor: the posts side and the comments side


def _epoch(iso):
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except (TypeError, ValueError):
        return 0


def _comment(n, username):
    post = n.get("postInfo") or {}
    sub = ((post.get("subreddit") or {}).get("prefixedName") or "").removeprefix("r/")
    post_id = (post.get("id") or "").removeprefix("t3_")
    preview = (n.get("content") or {}).get("preview") or ""
    body = preview + ("…" if len(preview) >= PREVIEW_LIMIT else "")
    return {
        "id":             (n.get("id") or "").removeprefix("t1_"),
        "author":         username,
        "body":           body,
        "body_html":      clean_reddit_html(_selftext_html({"markdown": body})) if body else "",
        "score":          n.get("score") or 0,
        "created_utc":    _epoch(n.get("createdAt")),
        "subreddit":      sub,
        "link_title":     post.get("title") or "",
        "link_permalink": f"https://www.reddit.com/r/{sub}/comments/{post_id}/" if sub and post_id else "",
        "link_id":        post_id,
    }


def fetch_gql_user_comments(username, sort="new", t="", after="", timeout=15):
    """{"comments", "after"}. Raises UpstreamError (404) when there's no such user."""
    variables = {"name": username, "sort": (sort if sort in SORTS else "new").upper(), "pageSize": FEED_LIMIT}
    if sort == "top" and t:
        variables["range"] = t.upper()
    if after:
        variables["after"] = to_gql_cursor(after)
    data = gql_query("SubmittedComments", variables, timeout)
    user = (data.get("data") or {}).get("redditorInfoByName")
    if not user:
        raise UpstreamError(GONE, 404)
    listing = user.get("comments") or {}
    page = listing.get("pageInfo") or {}
    comments = [_comment(e["node"], username) for e in listing.get("edges") or []
                if (e.get("node") or {}).get("__typename") == "Comment"]
    cursor = page.get("endCursor") if page.get("hasNextPage") else None
    return {"comments": comments, "after": from_gql_cursor(cursor) if cursor else None}


def _decode(after):
    try:
        return json.loads(base64.urlsafe_b64decode(after[len(OVERVIEW_PREFIX):]))
    except (ValueError, TypeError):
        return {}


def fetch_gql_user_overview(username, sort="new", t="", after="", timeout=15):
    """{"items": [{"type", "data"}], "after"}: one page of posts and one of comments, merged
    (newest first, or by score for top). Their next cursors are packed into one `after`
    (an empty side is finished), so pages are approximately, not strictly, chronological."""
    cursors = _decode(after) if after.startswith(OVERVIEW_PREFIX) else {}
    sides = {"p": cursors.get("p"), "c": cursors.get("c")}
    fetchers = {
        "p": lambda: fetch_gql_user_posts(username, sort, t, sides["p"] or "", timeout),
        "c": lambda: fetch_gql_user_comments(username, sort, t, sides["c"] or "", timeout),
    }
    live = [k for k in ("p", "c") if sides[k] != ""]   # "" marks a side with nothing left
    results = dict(zip(live, parallel(*(fetchers[k] for k in live)))) if live else {}
    items = [{"type": "post", "data": p} for p in (results.get("p") or {}).get("posts", [])]
    items += [{"type": "comment", "data": c} for c in (results.get("c") or {}).get("comments", [])]
    items.sort(key=lambda i: i["data"].get("score", 0) if sort == "top" else i["data"].get("created_utc", 0),
               reverse=True)
    nxt = {k: (results[k].get("after") or "") if k in results else "" for k in ("p", "c")}
    packed = base64.urlsafe_b64encode(json.dumps(nxt).encode()).decode()
    return {"items": items, "after": OVERVIEW_PREFIX + packed if any(nxt.values()) else None}
