"""Post comment trees and 'load more' children."""
from reddit.budget import budgeted
import time
from flask import Blueprint, request
from parsing.media_detection import process_post, _parse_awards, DISABLE_NSFW
from parsing.reddit_html import clean_reddit_html
from reddit.client import reddit_get
from reddit.archive import fetch_archived_comment_tree
from gql.client import fetch_gql_comments, decode_cursor, CURSOR_PREFIX
from gql.posts import fetch_gql_post
from core.helpers import (fetch_cache, CACHE_TTL_FEED, COMMENTS_LIMIT, COMMENT_SORTS, SUBREDDIT_RE, POST_ID_RE,
                     cached_json, error_response, server_cache, validate_params, hydrate_linked_posts,
                     is_waf_block, parallel, UpstreamError, log)
from routes.subreddit import _subreddit_error_state
from routes.avatars import _embed_comment_avatars

bp = Blueprint("comments", __name__)

# Comments loaded per "load more" click for a stub that isn't a GraphQL cursor: each is
# one GraphQL request, so the rest come back as another "load more".
MORECHILDREN_BATCH = 10


def _parse_comment_fields(d):
    edited = d.get("edited")
    edited_utc = edited if isinstance(edited, (int, float)) and edited else None
    return {
        "id":                    d["id"],
        "author":                d.get("author", "[deleted]"),
        "body":                  d.get("body", ""),
        "body_html":             clean_reddit_html(d.get("body_html")),
        "score":                 d.get("score", 0),
        "score_hidden":          bool(d.get("score_hidden")),
        "likes":                 d.get("likes"),
        "created_utc":           d.get("created_utc", 0),
        "edited_utc":            edited_utc,
        "depth":                 d.get("depth", 0),
        "replies":               [],
        "distinguished":         d.get("distinguished"),
        "is_submitter":          d.get("is_submitter", False),
        "stickied":              d.get("stickied", False),
        "author_flair_text":     d.get("author_flair_text") or "",
        "author_flair_richtext": d.get("author_flair_richtext") or [],
        "author_flair_type":     d.get("author_flair_type", "text"),
        "author_flair_bg":       d.get("author_flair_background_color") or "",
        "author_flair_tc":       d.get("author_flair_text_color") or "dark",
        "awards":                _parse_awards(d.get("all_awardings")),
    }


def _parse_comment_tree(c, author_fullnames):
    """A listing child (comment or "more" stub) with its replies, recursively.
    Records each author's fullname in `author_fullnames` for avatar lookup."""
    d = c["data"]
    if c["kind"] == "more":
        return {
            "kind":     "more",
            "id":       d.get("id", ""),
            "children": d.get("children", [])[:100],
            "count":    d.get("count", 0),
            "depth":    d.get("depth", 0),
        }
    replies = []
    if d.get("replies") and isinstance(d["replies"], dict):
        replies = [_parse_comment_tree(r, author_fullnames) for r in d["replies"]["data"]["children"]]
    comment = _parse_comment_fields(d)
    author_fullnames.setdefault(comment["author"], d.get("author_fullname"))
    comment["replies"] = replies
    return comment


# Attempts at by_id before giving up: Reddit's WAF 403s it now and then, independently of
# which device token sent the request, so a retry on the pool's next device usually works.
BY_ID_ATTEMPTS = 4


def _by_id_post(post_id):
    """A post's raw data from by_id; raises UpstreamError when Reddit won't give it."""
    for attempt in range(BY_ID_ATTEMPTS):
        resp = reddit_get(f"https://www.reddit.com/by_id/t3_{post_id}.json", params={"raw_json": 1},
                          timeout=12)
        if resp.status_code == 200:
            break
        if resp.status_code not in (403, 429) and resp.status_code < 500:
            break
        log.warning("by_id %s returned %s (attempt %d/%d)", post_id, resp.status_code,
                    attempt + 1, BY_ID_ATTEMPTS)
        if attempt < BY_ID_ATTEMPTS - 1:
            time.sleep(0.3 * (attempt + 1))
    if is_waf_block(resp):
        try:   # Reddit blocks this IP's REST requests: the app's GraphQL API still answers
            return fetch_gql_post(post_id)
        except UpstreamError:
            raise
        except Exception as e:
            log.warning("gql post failed post=%s: %s", post_id, e)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code, "Post not found")
    posts = resp.json()["data"]["children"]
    if not posts:
        raise UpstreamError("Post not found", 404)
    return posts[0]["data"]


def _gql_comments(post_id, comment_id, sort):
    """(post_raw, comments, author_fullnames): the post from by_id and the tree from the
    app's GraphQL API, fetched together."""
    post_raw, (comments, author_fullnames) = parallel(
        lambda: _by_id_post(post_id),
        lambda: fetch_gql_comments(post_id, sort, comment_id=comment_id))
    return post_raw, comments, author_fullnames


def _blocked_comments_fallback(post_id, comment_id):
    """(post_raw, comments, author_fullnames) when Reddit's WAF blocks the comment endpoint
    and GraphQL failed too: the post from by_id, the tree from the Arctic Shift archive."""
    post_raw = _by_id_post(post_id)
    try:
        author_fullnames = {}
        comments = [_parse_comment_tree(c, author_fullnames)
                    for c in fetch_archived_comment_tree(post_id, comment_id)]
    except Exception as e:
        log.warning("archived comment tree failed post=%s: %s", post_id, e)
        raise UpstreamError("Reddit is blocking comment loading right now", 403)
    return post_raw, comments, author_fullnames


def _listing_comments(subreddit, post_id, comment_id, sort, timeout):
    """(post_raw, comments, author_fullnames) from Reddit's comment-tree endpoint."""
    params = {"raw_json": 1, "limit": COMMENTS_LIMIT, "sort": sort}
    if comment_id:
        params["comment"] = comment_id
        params["context"] = 8
    # %2F before "comments": Reddit's WAF 403s the plain path for OAuth device tokens
    # (the same trick redlib uses); the encoded form reaches the same endpoint.
    resp = reddit_get(
        f"https://www.reddit.com/r/{subreddit}%2Fcomments/{post_id}.json",
        params=params, timeout=timeout)
    if resp.status_code == 403:
        state, _ = _subreddit_error_state(resp)
        if state == "quarantined":
            resp = reddit_get(
                f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json",
                quarantine=True, params=params, timeout=timeout)
    if resp.status_code == 403 and "json" not in resp.headers.get("content-type", ""):
        # Network-level WAF block (HTML page) on the comment-tree endpoints; posts and
        # listings still work, so take the post from by_id and the tree from the archive.
        return _blocked_comments_fallback(post_id, comment_id)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code, "Post not found")
    data  = resp.json()
    posts = data[0]["data"]["children"]
    if not posts:
        raise UpstreamError("Post not found", 404)
    author_fullnames = {}
    comments = [_parse_comment_tree(c, author_fullnames) for c in data[1]["data"]["children"]]
    return posts[0]["data"], comments, author_fullnames


@fetch_cache(CACHE_TTL_FEED, skip=lambda r: r.get('comments_error'))
@budgeted
def fetch_comments(subreddit, post_id, comment_id=None, sort='confidence', timeout=12, with_avatars=False):
    """A post plus its comment tree: {"post", "comments", "avatar_prefetch"}. With
    `comment_id`, the tree is that comment's thread (plus a few parents of context)."""
    if sort not in COMMENT_SORTS:
        sort = 'confidence'
    comments_error = None
    try:
        try:
            post_raw, comments, author_fullnames = _gql_comments(post_id, comment_id, sort)
        except Exception as e:
            log.warning("gql comments failed post=%s: %s", post_id, e)
            # Reddit's WAF blocks the comment-tree endpoint for OAuth device tokens, so the
            # archive goes first and the listing is only the last resort.
            try:
                post_raw, comments, author_fullnames = _blocked_comments_fallback(post_id, comment_id)
            except Exception as archive_err:
                log.warning("archived comments failed post=%s: %s", post_id, archive_err)
                post_raw, comments, author_fullnames = _listing_comments(
                    subreddit, post_id, comment_id, sort, timeout)
    except Exception as e:
        # The post and its comments load independently: when the combined fetch fails,
        # still show the post (via by_id) and report the comments as failed.
        log.warning("comment fetch failed post=%s: %s", post_id, e)
        try:
            posts = [_by_id_post(post_id)]
        except Exception:
            posts = []
        if not posts:
            raise e if isinstance(e, UpstreamError) else UpstreamError("Post not found", 502)
        post_raw, comments, author_fullnames = posts[0], [], {}
        comments_error = "Couldn't load comments"
    post     = process_post(post_raw)
    if DISABLE_NSFW and post.get("over_18"):
        raise UpstreamError("Post not found", 404)
    post["selftext"] = post_raw.get("selftext", "")   # full text in post view
    try:
        hydrate_linked_posts([post])
    except Exception as e:
        log.warning("linked post hydration failed post=%s: %s", post_id, e)

    try:
        avatar_prefetch = _embed_comment_avatars(comments, author_fullnames) if with_avatars else {}
    except Exception as e:
        log.warning("comment avatars failed post=%s: %s", post_id, e)
        avatar_prefetch = {}
    result = {"post": post, "comments": comments, "avatar_prefetch": avatar_prefetch}
    if comments_error:
        result["comments_error"] = comments_error
    return result


@bp.route("/api/r/<subreddit>/comments/<post_id>")
@validate_params(subreddit=SUBREDDIT_RE, post_id=POST_ID_RE)
@server_cache(CACHE_TTL_FEED)
def get_comments(subreddit, post_id):
    try:
        comment_id = request.args.get('comment')
        sort = request.args.get('sort', 'confidence')
        with_avatars = request.args.get('avatars') == '1'
        data = fetch_comments(subreddit, post_id, comment_id, sort, with_avatars=with_avatars)
        # A post whose comments failed must not be cached, so a reload retries them.
        return cached_json(data, 0 if data.get("comments_error") else CACHE_TTL_FEED)
    except UpstreamError as e:
        return e.response()
    except Exception:
        return error_response(500)


@budgeted
def fetch_morechildren(post_id, children, sort='confidence', timeout=12, with_avatars=False):
    """Expand a "load more comments" stub: `children` is its comma-separated comment ids.
    Returns {"comments", "avatar_prefetch"} with the loaded comments re-nested."""
    if sort not in COMMENT_SORTS:
        sort = "confidence"
    if not children:
        return {"comments": [], "avatar_prefetch": {}}
    if children.startswith(CURSOR_PREFIX):
        # a stub from the GraphQL fallback: its single child id is an encoded cursor
        try:
            roots, author_fullnames = fetch_gql_comments(post_id, sort, after=decode_cursor(children.split(",")[0]))
        except Exception:
            raise UpstreamError("Couldn't load these comments", 502)
        avatar_prefetch = _embed_comment_avatars(roots, author_fullnames) if with_avatars else {}
        return {"comments": roots, "avatar_prefetch": avatar_prefetch}
    return _morechildren_by_id(post_id, children, sort, with_avatars)


def _find_comment(comments, cid):
    for c in comments:
        if c.get("id") == cid and c.get("kind") != "more":
            return c
        found = _find_comment(c.get("replies") or [], cid)
        if found:
            return found
    return None


def _gql_comment_thread(post_id, cid, sort):
    """(comment with its replies, author_fullnames) from the app's GraphQL API, focused on
    that comment; None when GraphQL can't answer."""
    try:
        roots, author_fullnames = fetch_gql_comments(post_id, sort, comment_id=cid)
    except Exception as e:
        log.warning("gql comment thread failed post=%s comment=%s: %s", post_id, cid, e)
        return None
    comment = _find_comment(roots, cid)
    return (comment, author_fullnames) if comment else None


def _morechildren_by_id(post_id, children, sort, with_avatars):
    """fetch_morechildren's result for a stub listing plain comment ids (from the archive
    or the REST tree). Reddit's REST endpoints for this (/api/morechildren, comment
    permalinks) are WAF-blocked for device tokens, and a burst of those blocks pauses every
    upstream call, so they're never used: each comment comes from GraphQL, and the ones
    GraphQL can't load from the Arctic Shift archive. Only the first MORECHILDREN_BATCH ids
    are loaded; the rest come back as a trailing "more" stub."""
    ids = [i for i in children.split(",") if POST_ID_RE.match(i)]
    batch, rest = ids[:MORECHILDREN_BATCH], ids[MORECHILDREN_BATCH:]
    results = parallel(*[lambda cid=cid: _gql_comment_thread(post_id, cid, sort) for cid in batch]) if batch else []
    author_fullnames = {}
    found = {}
    for cid, r in zip(batch, results):
        if r:
            found[cid] = r[0]
            for name, fullname in r[1].items():
                author_fullnames.setdefault(name, fullname)
    missing = [cid for cid in batch if cid not in found]
    if missing:
        try:
            archived = [_parse_comment_tree(c, author_fullnames)
                        for c in fetch_archived_comment_tree(post_id)]
            for cid in missing:
                comment = _find_comment(archived, cid)
                if comment:
                    found[cid] = comment
        except Exception as e:
            log.warning("archived comments failed post=%s: %s", post_id, e)
    if batch and not found:
        raise UpstreamError("Couldn't load these comments", 502)
    roots = [found[cid] for cid in batch if cid in found]
    if rest:
        roots.append({"kind": "more", "id": rest[0], "children": rest,
                      "count": len(rest), "depth": 0})
    avatar_prefetch = _embed_comment_avatars(roots, author_fullnames) if with_avatars else {}
    return {"comments": roots, "avatar_prefetch": avatar_prefetch}


@bp.route("/api/r/<subreddit>/morechildren/<post_id>")
@validate_params(subreddit=SUBREDDIT_RE, post_id=POST_ID_RE)
@server_cache(CACHE_TTL_FEED)
def get_morechildren(subreddit, post_id):
    try:
        return cached_json(fetch_morechildren(post_id, request.args.get("children", ""),
                                              request.args.get("sort", "confidence"),
                                              with_avatars=request.args.get("avatars") == "1"),
                           CACHE_TTL_FEED)
    except UpstreamError as e:
        return e.response()
    except Exception:
        return error_response(500)
