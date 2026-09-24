"""Commenter profile-picture resolution: inline embedding for comment payloads and a batch endpoint."""
import re
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, jsonify, request
from media_detection import clean_url
from reddit_client import reddit_get
from helpers import USERNAME_RE, TTLCache, _CACHE_MISS, cached_json, log

bp = Blueprint("avatars", __name__)


AVATAR_EMBED_LIMIT    = 20   # icons embedded in the comments payload
AVATAR_PREFETCH_LIMIT = 200  # further commenters the client bulk-fetches right away
AVATAR_BATCH_CHUNK    = 200  # ids per bulk-lookup request; one big request beats several small

THREAD_MAX_DEPTH = 4  # static/render.js; deeper replies aren't rendered

_avatar_cache = TTLCache(5000, name='avatar')
AVATAR_CACHE_TTL = 6 * 3600


def _fetch_user_icon(username):
    hit = _avatar_cache.get(username)
    if hit is not _CACHE_MISS:
        return hit
    icon = None
    try:
        resp = reddit_get(f"https://www.reddit.com/user/{username}/about.json",
                           params={"raw_json": 1}, timeout=6)
        if resp.status_code == 200:
            d = resp.json().get("data", {})
            icon = clean_url(d.get("icon_img") or d.get("snoovatar_img") or "") or None
    except Exception as e:
        log.warning("avatar fetch failed user=%s: %s", username, e)
        icon = None
    _avatar_cache.set(username, icon, AVATAR_CACHE_TTL)
    return icon


def _collect_comment_authors_ordered(comments, seen, ordered, depth=0):
    """Unique authors in render order, down to THREAD_MAX_DEPTH."""
    for c in comments:
        if c.get("kind") == "more":
            continue
        author = c.get("author")
        if author and author != "[deleted]" and author not in seen:
            seen.add(author)
            ordered.append(author)
        if c.get("replies") and depth < THREAD_MAX_DEPTH:
            _collect_comment_authors_ordered(c["replies"], seen, ordered, depth + 1)


def _apply_comment_avatars(comments, icon_map, resolved_authors):
    """Set author_icon only for resolved authors: a missing key tells the client to lazy-load."""
    for c in comments:
        if c.get("kind") == "more":
            continue
        author = c.get("author")
        if author in resolved_authors:
            c["author_icon"] = icon_map.get(author) or None
        if c.get("replies"):
            _apply_comment_avatars(c["replies"], icon_map, resolved_authors)


def _fetch_user_icons_batch(pairs):
    """{author: icon} for [(author, fullname)] via Reddit's bulk account lookup
    (much faster than per-user about.json)."""
    result = {}
    to_fetch = {}  # fullname -> author
    for author, fullname in pairs:
        hit = _avatar_cache.get(author)
        if hit is not _CACHE_MISS:
            result[author] = hit
        elif fullname:
            to_fetch[fullname] = author
    fullnames = list(to_fetch.keys())
    for i in range(0, len(fullnames), AVATAR_BATCH_CHUNK):
        chunk = fullnames[i:i + AVATAR_BATCH_CHUNK]
        try:
            resp = reddit_get("https://www.reddit.com/api/user_data_by_account_ids",
                               params={"ids": ",".join(chunk)}, timeout=8)
            data = resp.json() if resp.status_code == 200 else {}
        except Exception as e:
            log.warning("avatar batch fetch failed (%d ids): %s", len(chunk), e)
            data = {}
        for fullname in chunk:
            author = to_fetch[fullname]
            icon = clean_url((data.get(fullname) or {}).get("profile_img") or "") or None
            result[author] = icon
            _avatar_cache.set(author, icon, AVATAR_CACHE_TTL)
    return result


def _resolve_icons(pairs):
    """{author: icon or None} for [(author, fullname or None)]: bulk lookup where the
    fullname is known, per-user requests otherwise."""
    result = _fetch_user_icons_batch([(a, f) for a, f in pairs if f])
    unbatchable = [a for a, f in pairs if not f]
    if unbatchable:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for author, icon in zip(unbatchable, ex.map(_fetch_user_icon, unbatchable)):
                result[author] = icon
    return result


def _embed_comment_avatars(comments, fullname_map=None):
    """Embed icons for the first AVATAR_EMBED_LIMIT commenters so they render with the
    comments. Returns {author: fullname} for the next AVATAR_PREFETCH_LIMIT, which the
    client bulk-fetches in the background."""
    seen = set()
    ordered = []
    _collect_comment_authors_ordered(comments, seen, ordered)
    if not ordered:
        return {}
    embed_authors = ordered[:AVATAR_EMBED_LIMIT]
    fullname_map = fullname_map or {}
    icon_map = _resolve_icons([(a, fullname_map.get(a)) for a in embed_authors])
    _apply_comment_avatars(comments, icon_map, set(embed_authors))
    prefetch_authors = ordered[AVATAR_EMBED_LIMIT:AVATAR_EMBED_LIMIT + AVATAR_PREFETCH_LIMIT]
    return {a: fullname_map[a] for a in prefetch_authors if fullname_map.get(a)}


FULLNAME_RE = re.compile(r'^t2_[A-Za-z0-9]{1,20}$')


@bp.route("/api/user/avatars")
def get_user_avatars():
    """Icons for commenters past the embed cutoff. `pairs` is name:fullname (bulk
    lookup); `names` is for commenters past the prefetch cap (per-user lookup)."""
    seen = set()
    pairs = []  # [(name, fullname_or_None), ...]
    for item in request.args.get("pairs", "").split(","):
        item = item.strip()
        if not item:
            continue
        name, _, fullname = item.partition(":")
        if name and name not in seen and name != "[deleted]" and USERNAME_RE.match(name):
            seen.add(name)
            pairs.append((name, fullname if FULLNAME_RE.match(fullname) else None))
    for n in request.args.get("names", "").split(","):
        n = n.strip()
        if n and n not in seen and n != "[deleted]" and USERNAME_RE.match(n):
            seen.add(n)
            pairs.append((n, None))
    pairs = pairs[:AVATAR_PREFETCH_LIMIT]
    if not pairs:
        return jsonify({})
    return cached_json(_resolve_icons(pairs), 3600)
