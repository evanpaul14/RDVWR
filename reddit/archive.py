"""Arctic Shift archive fetches — fallback for suspended/deleted/private user profiles."""
from parsing.media_detection import process_post, filter_nsfw
from parsing.reddit_html import clean_reddit_html
from reddit.client import SESSION
from core.helpers import FEED_LIMIT, UpstreamError, hydrate_linked_posts, log


ARCTIC_SHIFT_BASE = "https://arctic-shift.photon-reddit.com/api"


def arctic_shift_get(path, params, timeout=10):
    return SESSION.get(f"{ARCTIC_SHIFT_BASE}{path}", params=params, timeout=timeout)


def _normalize_comment(d):
    link_permalink = d.get("link_permalink", "") or d.get("permalink", "") or ""
    if link_permalink and not link_permalink.startswith("http"):
        link_permalink = f"https://www.reddit.com{link_permalink}"
    return {
        "id":             d.get("id", ""),
        "author":         d.get("author", "[deleted]"),
        "body":           d.get("body", ""),
        "body_html":      clean_reddit_html(d.get("body_html")),
        "score":          d.get("score", 0),
        "created_utc":    d.get("created_utc", 0),
        "subreddit":      d.get("subreddit", ""),
        "link_title":     d.get("link_title", ""),
        "link_permalink": link_permalink,
        "link_id":        (d.get("link_id") or "").replace("t3_", ""),
    }


def _backfill_comment_titles(comments):
    """Arctic Shift comments lack link_title/link_permalink; batch-fetch parent posts."""
    link_ids = sorted({c["link_id"] for c in comments if c["link_id"] and not c["link_title"]})
    if not link_ids:
        return
    try:
        resp = arctic_shift_get("/posts/ids", {"ids": ",".join(link_ids[:500])})
        resp.raise_for_status()
        posts = {p["id"]: p for p in resp.json().get("data", [])}
        for c in comments:
            p = posts.get(c["link_id"])
            if p:
                if not c["link_title"]:
                    c["link_title"] = p.get("title", "")
                if not c["link_permalink"]:
                    permalink = p.get("permalink", "")
                    c["link_permalink"] = f"https://www.reddit.com{permalink}" if permalink else ""
    except Exception as e:
        log.warning("archived comment link_title backfill failed: %s", e)


def _arctic_fetch(path, mapper, username, limit, before=None):
    """Shared Arctic Shift archive fetch: builds the author/sort/limit/before params,
    GETs `path`, and maps each raw item through `mapper` (skipping ones that raise)."""
    params = {"author": username, "sort": "desc", "limit": limit}
    if before:
        params["before"] = before
    resp = arctic_shift_get(path, params)
    resp.raise_for_status()
    items = []
    for d in resp.json().get("data", []):
        try:
            items.append(mapper(d))
        except Exception as e:
            log.warning("archived fetch mapper failed id=%s: %s", d.get("id"), e)
    return items


def _fetch_archived_posts(username, limit, before=None):
    return filter_nsfw(_arctic_fetch("/posts/search", process_post, username, limit, before))


def _fetch_archived_comments(username, limit, before=None):
    comments = _arctic_fetch("/comments/search", _normalize_comment, username, limit, before)
    _backfill_comment_titles(comments)
    return comments


def _arc_cursor(items, limit):
    """Next-page cursor for archived (Arctic Shift) listings: 'arc:<created_utc>'
    of the oldest item, only offered when the page was full (may be more)."""
    if len(items) < limit:
        return None
    return f"arc:{items[-1]['created_utc']}"


def _fetch_archived_overview(username, before=None):
    posts    = _fetch_archived_posts(username, FEED_LIMIT, before=before)
    comments = _fetch_archived_comments(username, FEED_LIMIT, before=before)
    hydrate_linked_posts(posts)
    items = (
        [{"type": "post", "data": p} for p in posts]
        + [{"type": "comment", "data": c} for c in comments]
    )
    items.sort(key=lambda i: i["data"].get("created_utc", 0), reverse=True)
    next_after = None
    if len(posts) == FEED_LIMIT or len(comments) == FEED_LIMIT:
        next_after = f"arc:{items[-1]['data']['created_utc']}" if items else None
    return items, next_after


def fetch_archived_comment_tree(post_id, comment_id=None, limit=500):
    """Reddit-shaped comment listing children for a post from Arctic Shift's tree endpoint.
    Fallback for when Reddit's WAF blocks the comment-tree endpoints. With `comment_id`, only
    that comment's subtree. Depths are filled in here (the archive omits them)."""
    params = {"link_id": post_id, "limit": limit}
    if comment_id:
        params["parent_id"] = f"t1_{comment_id}"
    resp = arctic_shift_get("/comments/tree", params, timeout=20)
    resp.raise_for_status()
    children = resp.json().get("data", [])

    def set_depth(nodes, depth):
        for n in nodes:
            n["data"]["depth"] = depth
            replies = n["data"].get("replies")
            if isinstance(replies, dict):
                set_depth(replies["data"]["children"], depth + 1)
    set_depth(children, 0)
    return children


def fetch_archived_post(post_id):
    """A post Reddit no longer returns (deleted by its author or otherwise dropped) from the
    archive, as a raw REST-shaped dict flagged `_archived`. Raises UpstreamError 404 when the
    archive doesn't have it either."""
    try:
        resp = arctic_shift_get("/posts/ids", {"ids": post_id})
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except Exception as e:
        log.warning("archived post fetch failed post=%s: %s", post_id, e)
        raise UpstreamError("Post not found", 404)
    if not data or data[0].get("id") != post_id:
        raise UpstreamError("Post not found", 404)
    return {**data[0], "_archived": True}


# Ids per Arctic Shift /comments/ids request, and per restore request overall.
ARCHIVED_IDS_MAX = 100
_GONE = ("", "[deleted]", "[removed]")


def fetch_archived_comment_bodies(comment_ids, timeout=15):
    """{id: {"author", "body"}} for the given comment ids, from the archive's copy taken
    before their author or text was deleted. "author" is "[deleted]" and "body" is "" when
    the archive only has that part after the deletion; ids with neither are left out."""
    ids = list(dict.fromkeys(comment_ids))[:ARCHIVED_IDS_MAX]
    if not ids:
        return {}
    resp = arctic_shift_get("/comments/ids", {"ids": ",".join(ids)}, timeout=timeout)
    resp.raise_for_status()
    wanted = set(ids)
    found = {}
    for d in resp.json().get("data") or []:
        cid = d.get("id")
        body = d.get("body") or ""
        author = d.get("author") or ""
        body = "" if body.strip() in _GONE else body
        author = "[deleted]" if author in _GONE else author
        if cid in wanted and (body or author != "[deleted]"):
            found[cid] = {"author": author, "body": body}
    return found
