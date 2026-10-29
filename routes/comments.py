"""Post comment trees and 'load more' children."""
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, request
from media_detection import process_post, _parse_awards, DISABLE_NSFW
from reddit_html import clean_reddit_html
from reddit_client import reddit_get
from archive import fetch_archived_comment_tree
from gql_client import fetch_gql_comments, decode_cursor, CURSOR_PREFIX
from helpers import (CACHE_TTL_FEED, COMMENTS_LIMIT, COMMENT_SORTS, SUBREDDIT_RE, POST_ID_RE,
                     cached_json, error_response, server_cache, validate_params, hydrate_linked_posts,
                     parallel, UpstreamError, log)
from routes.subreddit import _subreddit_error_state
from routes.avatars import _embed_comment_avatars

bp = Blueprint("comments", __name__)

# Comments fetched per "load more" click when falling back to permalink requests.
MORECHILDREN_PERMALINK_BATCH = 20


def _parse_comment_fields(d):
    edited = d.get("edited")
    edited_utc = edited if isinstance(edited, (int, float)) and edited else None
    return {
        "id":                    d["id"],
        "author":                d.get("author", "[deleted]"),
        "body":                  d.get("body", ""),
        "body_html":             clean_reddit_html(d.get("body_html")),
        "score":                 d.get("score", 0),
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


def _by_id_post(post_id):
    """A post's raw data from by_id; raises UpstreamError when Reddit won't give it."""
    resp = reddit_get(f"https://www.reddit.com/by_id/t3_{post_id}.json", params={"raw_json": 1}, timeout=12)
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
            post_raw, comments, author_fullnames = _listing_comments(
                subreddit, post_id, comment_id, sort, timeout)
    except Exception as e:
        # The post and its comments load independently: when the combined fetch fails,
        # still show the post (via by_id) and report the comments as failed.
        log.warning("comment fetch failed post=%s: %s", post_id, e)
        try:
            resp = reddit_get(f"https://www.reddit.com/by_id/t3_{post_id}.json",
                              params={"raw_json": 1}, timeout=timeout)
            posts = resp.json()["data"]["children"] if resp.status_code == 200 else []
        except Exception:
            posts = []
        if not posts:
            raise e if isinstance(e, UpstreamError) else UpstreamError("Post not found", 502)
        post_raw, comments, author_fullnames = posts[0]["data"], [], {}
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
    resp = reddit_get(
        "https://www.reddit.com/api%2Fmorechildren.json",
        params={"link_id": f"t3_{post_id}", "children": children, "sort": sort,
                "api_type": "json", "raw_json": 1},
        timeout=timeout)
    if resp.status_code == 403:
        # Reddit refuses /api/morechildren for the OAuth device tokens, but comment
        # permalinks still load — fetch each child's thread that way instead.
        return _morechildren_via_permalinks(post_id, children, sort, timeout, with_avatars)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    things = resp.json().get("json", {}).get("data", {}).get("things", [])
    by_id  = {}
    ordered = []
    author_fullnames = {}
    for thing in things:
        if thing["kind"] != "t1":
            continue
        d = thing["data"]
        comment = _parse_comment_fields(d)
        comment["_pid"] = d.get("parent_id", "")
        author_fullnames.setdefault(comment["author"], d.get("author_fullname"))
        by_id[d["id"]] = comment
        ordered.append(comment)
    roots = []
    for c in ordered:
        pid = c.pop("_pid", "")
        if pid.startswith("t1_"):
            parent = by_id.get(pid[3:])
            if parent:
                parent["replies"].append(c)
                continue
        roots.append(c)
    avatar_prefetch = _embed_comment_avatars(roots, author_fullnames) if with_avatars else {}
    return {"comments": roots, "avatar_prefetch": avatar_prefetch}


def _morechildren_via_permalinks(post_id, children, sort, timeout, with_avatars):
    """fetch_morechildren's result built from one permalink request per comment id. Only
    the first MORECHILDREN_PERMALINK_BATCH ids are fetched; the rest come back as a
    trailing "more" stub so the page can offer another "load more"."""
    ids = [i for i in children.split(",") if POST_ID_RE.match(i)]
    batch, rest = ids[:MORECHILDREN_PERMALINK_BATCH], ids[MORECHILDREN_PERMALINK_BATCH:]

    def fetch_one(cid):
        resp = reddit_get(f"https://www.reddit.com/r/all%2Fcomments/{post_id}.json",
                          params={"raw_json": 1, "comment": cid, "sort": sort}, timeout=timeout)
        if resp.status_code != 200:
            raise UpstreamError.from_status(resp.status_code)
        return [c for c in resp.json()[1]["data"]["children"] if c["kind"] == "t1"]

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda cid: _attempt_fetch(fetch_one, cid), batch))
    if batch and all(r is None for r in results):
        raise UpstreamError("Couldn't load these comments", 502)
    author_fullnames = {}
    roots = [_parse_comment_tree(c, author_fullnames) for r in results if r for c in r]
    if rest:
        roots.append({"kind": "more", "id": rest[0], "children": rest,
                      "count": len(rest), "depth": 0})
    avatar_prefetch = _embed_comment_avatars(roots, author_fullnames) if with_avatars else {}
    return {"comments": roots, "avatar_prefetch": avatar_prefetch}


def _attempt_fetch(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


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
