"""Post comment trees and 'load more' children."""
from flask import Blueprint, jsonify, request
from media_detection import process_post, _parse_awards
from reddit_client import reddit_get
from helpers import (CACHE_TTL_FEED, COMMENTS_LIMIT, SUBREDDIT_RE, POST_ID_RE,
                     cached_json, error_response, server_cache, validate_params, hydrate_linked_posts)
from routes.subreddit import _subreddit_error_state
from routes.avatars import _embed_comment_avatars

bp = Blueprint("comments", __name__)


COMMENT_SORTS = {'confidence', 'top', 'new', 'controversial', 'old', 'qa'}


def _parse_comment_fields(d):
    edited = d.get("edited")
    edited_utc = edited if isinstance(edited, (int, float)) and edited else None
    return {
        "id":                    d["id"],
        "author":                d.get("author", "[deleted]"),
        "body":                  d.get("body", ""),
        "score":                 d.get("score", 0),
        "created_utc":           d.get("created_utc", 0),
        "edited_utc":            edited_utc,
        "depth":                 d.get("depth", 0),
        "replies":               [],
        "distinguished":         d.get("distinguished"),
        "stickied":              d.get("stickied", False),
        "author_flair_text":     d.get("author_flair_text") or "",
        "author_flair_richtext": d.get("author_flair_richtext") or [],
        "author_flair_type":     d.get("author_flair_type", "text"),
        "author_flair_bg":       d.get("author_flair_background_color") or "",
        "author_flair_tc":       d.get("author_flair_text_color") or "dark",
        "awards":                _parse_awards(d.get("all_awardings")),
    }


def _fetch_comments_data(subreddit, post_id, comment_id=None, sort='confidence', timeout=12, with_avatars=False):
    """Fetch + parse a post's comments. Returns (data_dict, None) or (None, (error_msg, status))."""
    if sort not in COMMENT_SORTS:
        sort = 'confidence'
    params = {"raw_json": 1, "limit": COMMENTS_LIMIT, "sort": sort}
    if comment_id:
        params["comment"] = comment_id
        params["context"] = 8
    resp = reddit_get(
        f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json",
        params=params, timeout=timeout)
    if resp.status_code == 403:
        state, _ = _subreddit_error_state(resp)
        if state == "quarantined":
            resp = reddit_get(
                f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json",
                quarantine=True, params=params, timeout=timeout)
    if resp.status_code != 200:
        return None, (f"Reddit returned {resp.status_code}", resp.status_code)
    data     = resp.json()
    children = data[0]["data"]["children"]
    if not children:
        return None, ("Post not found", 404)
    post_raw = children[0]["data"]
    post     = process_post(post_raw)
    post["selftext"] = post_raw.get("selftext", "")   # full text in post view
    hydrate_linked_posts([post])

    author_fullnames = {}

    def parse_comment(c):
        if c["kind"] == "more":
            d = c["data"]
            return {
                "kind":     "more",
                "id":       d.get("id", ""),
                "children": d.get("children", [])[:100],
                "count":    d.get("count", 0),
                "depth":    d.get("depth", 0),
            }
        d       = c["data"]
        replies = []
        if d.get("replies") and isinstance(d["replies"], dict):
            for r in d["replies"]["data"]["children"]:
                parsed = parse_comment(r)
                if parsed:
                    replies.append(parsed)
        comment = _parse_comment_fields(d)
        author_fullnames.setdefault(comment["author"], d.get("author_fullname"))
        comment["replies"] = replies
        return comment

    comments = [c for c in (parse_comment(c) for c in data[1]["data"]["children"]) if c]
    avatar_prefetch = _embed_comment_avatars(comments, author_fullnames) if with_avatars else {}
    return {"post": post, "comments": comments, "avatar_prefetch": avatar_prefetch}, None


@bp.route("/api/r/<subreddit>/comments/<post_id>")
@validate_params(subreddit=SUBREDDIT_RE, post_id=POST_ID_RE)
@server_cache(CACHE_TTL_FEED)
def get_comments(subreddit, post_id):
    try:
        comment_id = request.args.get('comment')
        sort = request.args.get('sort', 'confidence')
        with_avatars = request.args.get('avatars') == '1'
        data, err = _fetch_comments_data(subreddit, post_id, comment_id, sort, with_avatars=with_avatars)
        if err:
            msg, status = err
            return jsonify({"error": msg}), status
        return cached_json(data, CACHE_TTL_FEED)
    except Exception:
        return error_response(500)


@bp.route("/api/r/<subreddit>/morechildren/<post_id>")
@validate_params(subreddit=SUBREDDIT_RE, post_id=POST_ID_RE)
@server_cache(CACHE_TTL_FEED)
def get_morechildren(subreddit, post_id):
    children = request.args.get("children", "")
    sort     = request.args.get("sort", "confidence")
    with_avatars = request.args.get("avatars") == "1"
    if sort not in COMMENT_SORTS:
        sort = "confidence"
    if not children:
        return cached_json({"comments": []}, CACHE_TTL_FEED)
    try:
        resp = reddit_get(
            "https://www.reddit.com/api/morechildren.json",
            params={"link_id": f"t3_{post_id}", "children": children, "sort": sort,
                    "api_type": "json", "raw_json": 1},
            timeout=12)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
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
        return cached_json({"comments": roots, "avatar_prefetch": avatar_prefetch}, CACHE_TTL_FEED)
    except Exception:
        return error_response(500)
