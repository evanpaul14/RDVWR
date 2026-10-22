"""Comment trees from the Reddit Android app's GraphQL endpoint (gql-fed.reddit.com).

Reddit's WAF blocks every REST comment-tree endpoint for the OAuth device tokens, but the
app's own persisted GraphQL query still answers. Hashes are the app's (see Amqx/reddit-gql);
Reddit may retire them. Results are converted to the same dicts routes/comments.py builds."""
import base64
import re
from datetime import datetime
from media_detection import clean_url
from reddit_client import _get_device
from helpers import log

GQL_URL = "https://gql-fed.reddit.com/"
POST_COMMENTS_HASH = "314f29c2494fd78c7844ef2c6b09acb4bef5b37ab50b4ddc01691d6d5c8433f8"
CURSOR_PREFIX = "gql:"   # marks a "more" stub's child id as an encoded GraphQL cursor
GQL_CURSOR_RE = re.compile(r"^gql:[A-Za-z0-9_=-]{1,600}$")
TOP_LEVEL_COUNT = 100
MAX_DEPTH = 8


def encode_cursor(cursor):
    """A GraphQL cursor as a URL/comma-safe stub child id."""
    return CURSOR_PREFIX + base64.urlsafe_b64encode(cursor.encode()).decode()


def decode_cursor(child):
    return base64.urlsafe_b64decode(child[len(CURSOR_PREFIX):].encode()).decode()


def _epoch(iso):
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except ValueError:
        return None


def _comment(n, depth):
    author = n.get("authorInfo") or {}
    flair = n.get("authorFlair") or {}
    template = flair.get("template") or {}
    awards = []
    for a in sorted(n.get("awardings") or [], key=lambda a: -a.get("total", 0))[:5]:
        icon = clean_url(((a.get("award") or {}).get("static_icon_32") or {}).get("url", ""))
        if icon:
            awards.append({"name": a["award"].get("name", ""), "count": a.get("total", 1), "icon": icon})
    vote = n.get("voteState")
    return {
        "id":                    n["id"].removeprefix("t1_"),
        "author":                author.get("name") or "[deleted]",
        "body":                  (n.get("content") or {}).get("markdown") or "",
        "body_html":             "",
        "score":                 n.get("score") or 0,
        "likes":                 True if vote == "UPVOTE" else False if vote == "DOWNVOTE" else None,
        "created_utc":           _epoch(n.get("createdAt")) or 0,
        "edited_utc":            _epoch(n.get("editedAt")),
        "depth":                 depth,
        "replies":               [],
        "distinguished":         (n.get("distinguishedAs") or "").lower() or None,
        "is_submitter":          bool(n.get("isOP")),
        "stickied":              bool(n.get("isStickied")),
        "author_flair_text":     flair.get("text") or "",
        "author_flair_richtext": [],
        "author_flair_type":     "text",
        "author_flair_bg":       template.get("backgroundColor") or "",
        "author_flair_tc":       (flair.get("textColor") or "dark").lower(),
        "awards":                awards,
    }


def _post(body, timeout):
    resp = None
    for attempt in range(2):
        device = _get_device()
        headers = {**device.api_headers(), "Content-Type": "application/json"}
        resp = device.session.post(GQL_URL, headers=headers, json=body,
                                   impersonate=device.impersonate, timeout=timeout)
        if resp.status_code == 401 and attempt == 0:
            device.expires_at = 0.0  # force this device to re-auth next use
            continue
        break
    return resp


def fetch_gql_comments(post_id, sort="confidence", after=None, comment_id=None, timeout=15):
    """(roots, author_fullnames): a post's comment tree, nested like fetch_comments builds it.
    "more" stubs carry one encoded cursor in `children`; pass the decoded cursor back as
    `after` to load it. `comment_id` narrows to that comment's thread."""
    variables = {"id": f"t3_{post_id}", "sortType": sort.upper(), "count": TOP_LEVEL_COUNT,
                 "maxDepth": MAX_DEPTH, "includePremiumAvatarTreatment": False,
                 "includeConsistencyToken": False}
    if after:
        variables["after"] = after
    if comment_id:
        variables["focusedCommentContext"] = {"commentId": comment_id, "isFocusedThreadOnly": True}
    body = {"operationName": "PostComments", "variables": variables,
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": POST_COMMENTS_HASH}}}
    resp = _post(body, timeout)
    data = resp.json() if resp.status_code == 200 else {}
    if data.get("errors") or not (data.get("data") or {}).get("postInfoById"):
        log.warning("gql comments failed post=%s status=%s errors=%s",
                    post_id, resp.status_code, (data.get("errors") or [{}])[0].get("message"))
        raise RuntimeError("GraphQL comment fetch failed")

    by_id, roots, author_fullnames = {}, [], {}
    for entry in data["data"]["postInfoById"]["commentForest"]["trees"]:
        node, more = entry.get("node"), entry.get("more")
        parent = by_id.get((entry.get("parentId") or "").removeprefix("t1_"))
        siblings = parent["replies"] if parent else roots
        if node and node.get("__typename") == "Comment":
            c = _comment(node, entry.get("depth", 0))
            author_fullnames.setdefault(c["author"], (node.get("authorInfo") or {}).get("id"))
            by_id[c["id"]] = c
            siblings.append(c)
        elif more and more.get("cursor"):
            siblings.append({"kind": "more", "id": entry.get("parentId") or "top",
                             "children": [encode_cursor(more["cursor"])],
                             "count": more.get("count", 0), "depth": entry.get("depth", 0)})
    return roots, author_fullnames
