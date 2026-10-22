"""Comment trees from the Reddit Android app's GraphQL endpoint (gql-fed.reddit.com).

Reddit's WAF blocks every REST comment-tree endpoint for the OAuth device tokens, but the
app's own persisted GraphQL query still answers. The query hashes live in gql_hashes.json,
kept current from the latest APK by .github/workflows/update-gql-hash.yml; when Reddit
rejects ours we re-read that file from GitHub, so a rotation heals without a deploy; and if
GITHUB_DISPATCH_TOKEN is set, a failure also kicks that workflow off right away.
Results are converted to the same dicts routes/comments.py builds."""
import base64
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from media_detection import clean_url
from reddit_client import _get_device, SESSION
from helpers import log

GQL_URL = "https://gql-fed.reddit.com/"
HASHES_FILE = Path(__file__).parent / "gql_hashes.json"
HASHES_URL = "https://raw.githubusercontent.com/evanpaul14/RDVWR/main/gql_hashes.json"
HASHES_REFRESH_MIN_INTERVAL = 600   # seconds between GitHub re-reads
WORKFLOW_DISPATCH_URL = ("https://api.github.com/repos/evanpaul14/RDVWR/actions/workflows/"
                         "update-gql-hash.yml/dispatches")
WORKFLOW_MIN_INTERVAL = 3600        # seconds between workflow runs we trigger
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
CURSOR_PREFIX = "gql:"   # marks a "more" stub's child id as an encoded GraphQL cursor
GQL_CURSOR_RE = re.compile(r"^gql:[A-Za-z0-9_=-]{1,600}$")
TOP_LEVEL_COUNT = 100
MAX_DEPTH = 8


def _valid_hashes(d):
    return isinstance(d, dict) and all(isinstance(k, str) and isinstance(v, str) and HASH_RE.match(v)
                                       for k, v in d.items())


_hashes = json.loads(HASHES_FILE.read_text())
_last_refresh = 0.0
_last_dispatch = 0.0


def _refresh_hashes():
    """Re-read the hashes from GitHub (throttled). True when any of them changed."""
    global _hashes, _last_refresh
    if time.time() - _last_refresh < HASHES_REFRESH_MIN_INTERVAL:
        return False
    _last_refresh = time.time()
    try:
        fresh = SESSION.get(HASHES_URL, timeout=10).json()
    except Exception as e:
        log.warning("gql hash refresh failed: %s", e)
        return False
    if not _valid_hashes(fresh) or all(_hashes.get(k) == v for k, v in fresh.items()):
        return False
    _hashes = {**_hashes, **fresh}
    log.info("gql hashes updated from GitHub")
    return True


def _dispatch_workflow():
    """Ask GitHub to run the hash-update workflow (throttled, needs GITHUB_DISPATCH_TOKEN: a
    fine-grained token with Actions read/write on the repo). Fire-and-forget."""
    global _last_dispatch
    token = os.environ.get("GITHUB_DISPATCH_TOKEN", "").strip()
    if not token or time.time() - _last_dispatch < WORKFLOW_MIN_INTERVAL:
        return
    _last_dispatch = time.time()

    def run():
        try:
            resp = SESSION.post(WORKFLOW_DISPATCH_URL, json={"ref": "main"}, timeout=10, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
            log.info("gql hash workflow dispatched: HTTP %s", resp.status_code)
        except Exception as e:
            log.warning("gql hash workflow dispatch failed: %s", e)
    threading.Thread(target=run, daemon=True).start()


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
    for attempt in range(2):
        body = {"operationName": "PostComments", "variables": variables,
                "extensions": {"persistedQuery": {"version": 1, "sha256Hash": _hashes["PostComments"]}}}
        resp = _post(body, timeout)
        data = resp.json() if resp.status_code == 200 else {}
        if not data.get("errors") and (data.get("data") or {}).get("postInfoById"):
            break
        log.warning("gql comments failed post=%s status=%s errors=%s",
                    post_id, resp.status_code, (data.get("errors") or [{}])[0].get("message"))
        if attempt == 1 or not _refresh_hashes():
            _dispatch_workflow()
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
