"""Arctic Shift archive fetches — fallback for suspended/deleted/private user profiles."""
from media_detection import process_post, filter_nsfw
from reddit_html import clean_reddit_html
from reddit_client import SESSION
from helpers import FEED_LIMIT, hydrate_linked_posts, log


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
    """GET an author's newest items from `path`, mapped through `mapper` (failures skipped)."""
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
    """'arc:<created_utc>' of the oldest item, only when the page was full."""
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
