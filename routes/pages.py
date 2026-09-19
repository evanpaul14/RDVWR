"""SPA page routes (with server-side data injection) and the raw .json Reddit passthrough."""
import re
from urllib.parse import urlencode
from flask import Blueprint, jsonify, request, render_template, Response, redirect, make_response
from media_detection import extract_posts, clean_url, process_post, clean_reddit_html
from reddit_client import reddit_get
from helpers import FEED_LIMIT, FEED_SORTS, DISABLE_DOWNLOADS, add_time_param, parallel, log
from routes.users import _fetch_user_about, _fetch_user_overview
from routes.comments import _fetch_comments_data, COMMENT_SORTS

SEARCH_SORTS = {'relevance', 'hot', 'top', 'new'}
SEARCH_TYPES = {'posts', 'communities', 'users'}

bp = Blueprint("pages", __name__)


_SUB_FEED_RE = re.compile(r'^([A-Za-z0-9_]+)(?:/(hot|new|top|rising|controversial))?$')
_POST_PERMALINK_RE = re.compile(r'^([A-Za-z0-9_]+)/comments/([A-Za-z0-9]+)(?:/[^/]*(?:/([A-Za-z0-9]+))?)?/?$')

# Allowlist for the raw ".json" passthrough below: only the same public content
# shapes rdvwr's own endpoints already expose (subreddit feeds/about/wiki, post
# permalinks, user profiles, search) — never arbitrary oauth.reddit.com paths
# like /api/v1/me.json or /api/morechildren.json, which would let any visitor
# use the server's pooled OAuth credentials as an open proxy into Reddit's
# authenticated API.
_JSON_PASSTHROUGH_RE = re.compile(
    r'^(?:'
    r'r/[A-Za-z0-9_+]{1,100}(?:/(?:hot|new|top|rising|controversial'
    r'|about|about/rules|about/moderators'
    r'|comments/[A-Za-z0-9]{1,10}(?:/[^/]*(?:/[A-Za-z0-9]{1,10})?)?'
    r'|duplicates/[A-Za-z0-9]{1,10}'
    r'|wiki(?:/[A-Za-z0-9_\-/]+)?))?'
    r'|u(?:ser)?/[A-Za-z0-9_-]{1,50}(?:/(?:about|submitted|comments|overview|trophies))?'
    r'|search'
    r')\.json$'
)


# ── SPA catch-all routes ──────────────────────────────────────────────────────

@bp.route("/", strict_slashes=False)
@bp.route("/home", strict_slashes=False)
@bp.route("/home/<sort>", strict_slashes=False)
@bp.route("/user/<username>", strict_slashes=False)
@bp.route("/user/<username>/m/<multiname>", strict_slashes=False)
@bp.route("/user/<username>/m/<multiname>/<sort>", strict_slashes=False)
@bp.route("/u/<username>", strict_slashes=False)
@bp.route("/search", strict_slashes=False)
@bp.route("/saved", strict_slashes=False)
@bp.route("/subscribed", strict_slashes=False)
@bp.route("/subscribed/<sort>", strict_slashes=False)
@bp.route("/r/<subreddit>/duplicates/<post_id>", strict_slashes=False)
@bp.route("/r/<subreddit>/wiki", strict_slashes=False)
@bp.route("/r/<subreddit>/wiki/<path:page>", strict_slashes=False)
@bp.route("/live/<path:path>", strict_slashes=False)
def spa(**kwargs):
    initial_profile = initial_data = initial_about = None
    ns_search = ns_wiki = ns_duplicates = ns_multi = None
    username = kwargs.get('username')
    path = request.path

    if username and 'multiname' in kwargs:
        sort = kwargs.get('sort') or 'hot'
        if sort not in FEED_SORTS:
            sort = 'hot'
        t = request.args.get('t', 'all')
        after = request.args.get('after', '')
        ns_multi = _try_inject_multi(username, kwargs['multiname'], sort, t, after)
    elif username:
        initial_profile = _try_inject_profile(username, request.args.get('after', ''))
    elif path == '/' or path.startswith('/home'):
        sort = kwargs.get('sort') or 'hot'
        if sort not in FEED_SORTS:
            sort = 'hot'
        t = request.args.get('t', 'all')
        after = request.args.get('after', '')
        initial_data, initial_about = _try_inject_subreddit('popular', sort, t, after)
    elif path == '/search':
        ns_search = _try_inject_search()
    elif 'post_id' in kwargs:
        ns_duplicates = _try_inject_duplicates(kwargs['subreddit'], kwargs['post_id'])
    elif 'subreddit' in kwargs:
        ns_wiki = _try_inject_wiki(kwargs['subreddit'], kwargs.get('page', 'index'))

    resp = render_template("index.html", initial_profile=initial_profile, initial_data=initial_data,
                           initial_about=initial_about, ns_search=ns_search, ns_wiki=ns_wiki,
                           ns_duplicates=ns_duplicates, ns_multi=ns_multi, disable_downloads=DISABLE_DOWNLOADS,
                           ns_hls=_ns_hls_enabled())
    return resp, 200, {'Cache-Control': 'no-store'}


def _ns_hls_enabled():
    """Whether the no-JS fallback should embed HLS (.m3u8) video sources instead of the
    plain mp4 fallback. Off by default since only Safari plays HLS natively without JS;
    toggled per-visitor via the /ns-hls link shown under videos in the noscript view."""
    return request.cookies.get('ns_hls') == '1'


@bp.route("/ns-hls")
def toggle_ns_hls():
    """Plain-link (no-JS-compatible) toggle for the noscript HLS-video preference cookie."""
    enable = request.args.get('enable') == '1'
    next_path = request.args.get('next') or '/'
    if not next_path.startswith('/') or next_path.startswith('//'):
        next_path = '/'
    resp = make_response(redirect(next_path))
    if enable:
        resp.set_cookie('ns_hls', '1', max_age=31536000, samesite='Lax')
    else:
        resp.delete_cookie('ns_hls')
    return resp


_NS_TIME_OPTIONS = ('hour', 'day', 'week', 'month', 'year', 'all')


def _ns_url(path, **params):
    """Build a plain-link URL for the noscript pagination/time/sort controls: query
    params with falsy values are dropped so links stay minimal."""
    qs = {k: v for k, v in params.items() if v}
    return path + ('?' + urlencode(qs) if qs else '')


def _try_inject_profile(username, after=''):
    """Fetch a user's about + overview in parallel for SSR injection."""
    def _about():
        try:
            data, err = _fetch_user_about(username, timeout=6)
            return None if err else data
        except Exception as e:
            log.warning("inject profile about user=%s: %s", username, e)
            return None

    def _overview():
        try:
            data, err = _fetch_user_overview(username, after=after, timeout=6, allow_archive=False)
            return None if err else data
        except Exception as e:
            log.warning("inject profile overview user=%s: %s", username, e)
            return None

    about, overview = parallel(_about, _overview)
    if overview is None:
        return None
    overview["_username"] = username.lower()
    overview["_about"] = about
    if overview.get("after"):
        overview["_next_url"] = _ns_url(f"/user/{username}", after=overview["after"])
    return overview


def _try_inject_subreddit(sub, sort, time, after=''):
    """Fetch subreddit feed + about in parallel for SSR injection.
    Returns (feed_dict, about_dict); either may be None on error."""
    def _feed():
        try:
            url = f"https://www.reddit.com/r/{sub.replace('+', '%2B')}/{sort}.json"
            params = {"limit": FEED_LIMIT, "raw_json": 1}
            add_time_param(params, sort, time)
            if after:
                params["after"] = after
            r = reddit_get(url, params=params, timeout=6)
            if r.status_code != 200:
                return None
            listing = r.json()["data"]
            next_after = listing.get("after")
            base = f"/r/{sub.lower()}/{sort}"
            result = {"posts": extract_posts(listing), "after": next_after,
                      "_sub": sub.lower(), "_sort": sort, "_time": time}
            if sort in ('top', 'controversial'):
                result["_time_links"] = {opt: _ns_url(base, t=opt if opt != 'all' else '') for opt in _NS_TIME_OPTIONS}
            if next_after:
                result["_next_url"] = _ns_url(base, t=time if time != 'all' else '', after=next_after)
            return result
        except Exception as e:
            log.warning("inject feed sub=%s: %s", sub, e)
            return None

    def _about():
        try:
            r = reddit_get(f"https://www.reddit.com/r/{sub}/about.json",
                           params={"raw_json": 1}, timeout=5)
            if r.status_code != 200:
                return None
            d = r.json()["data"]
            icon = clean_url(d.get("icon_img") or d.get("community_icon") or "")
            active = d.get("active_user_count") or d.get("accounts_active") or 0
            sidebar_html = clean_reddit_html(d.get("description_html"))
            return {"title": d.get("title", sub), "description": d.get("public_description", ""),
                    "sidebar": sidebar_html, "subscribers": d.get("subscribers", 0),
                    "active": active, "icon": icon or "", "_sub": sub.lower()}
        except Exception as e:
            log.warning("inject about sub=%s: %s", sub, e)
            return None

    def _rules():
        try:
            r = reddit_get(f"https://www.reddit.com/r/{sub}/about/rules.json",
                           params={"raw_json": 1}, timeout=5)
            if r.status_code != 200:
                return []
            return [ru.get("short_name", "") for ru in r.json().get("rules", []) if ru.get("short_name")]
        except Exception as e:
            log.warning("inject rules sub=%s: %s", sub, e)
            return []

    def _mods():
        try:
            r = reddit_get(f"https://www.reddit.com/r/{sub}/about/moderators.json",
                           params={"raw_json": 1}, timeout=5)
            if r.status_code != 200:
                return []
            children = r.json().get("data", {}).get("children", [])
            return [m.get("name", "") for m in children if m.get("name")]
        except Exception as e:
            log.warning("inject mods sub=%s: %s", sub, e)
            return []

    feed, about, rules, mods = parallel(_feed, _about, _rules, _mods)
    if about is not None:
        about["_rules"] = rules
        about["_mods"] = mods
    return feed, about


def _try_inject_multi(username, multiname, sort, time, after=''):
    """Fetch a user multireddit's combined feed for SSR injection."""
    try:
        meta = reddit_get(f"https://www.reddit.com/api/multi/user/{username}/m/{multiname}.json",
                          params={"raw_json": 1}, timeout=6)
        if meta.status_code != 200:
            return {"error": f"Reddit returned {meta.status_code}" if meta.status_code != 404 else "Multireddit not found"}
        meta_data = meta.json().get("data", {})
        subs = [s["name"] for s in meta_data.get("subreddits", [])]
        display = meta_data.get("display_name") or meta_data.get("name") or multiname
        if not subs:
            return {"posts": [], "after": None, "title": display, "_username": username.lower(),
                    "_multiname": multiname, "_sort": sort}
        combined = "+".join(subs[:100])
        params = {"limit": FEED_LIMIT, "raw_json": 1}
        add_time_param(params, sort, time)
        if after:
            params["after"] = after
        r = reddit_get(f"https://www.reddit.com/r/{combined}/{sort}.json", params=params, timeout=6)
        if r.status_code != 200:
            return {"error": f"Reddit returned {r.status_code}"}
        listing = r.json()["data"]
        next_after = listing.get("after")
        base = f"/user/{username}/m/{multiname}/{sort}"
        result = {"posts": extract_posts(listing), "after": next_after, "title": display,
                  "_username": username.lower(), "_multiname": multiname, "_sort": sort}
        if next_after:
            result["_next_url"] = _ns_url(base, t=time if time != 'all' else '', after=next_after)
        return result
    except Exception as e:
        log.warning("inject multi user=%s multi=%s: %s", username, multiname, e)
        return {"error": "Failed to load multireddit"}


def _try_inject_search():
    """Fetch post/community/user search results for SSR injection (noscript search
    form / results page). `stype` selects which of Reddit's search kinds to query."""
    q = request.args.get('q', '').strip()
    flair = request.args.get('flair', '').strip()
    stype = request.args.get('stype', 'posts')
    if stype not in SEARCH_TYPES:
        stype = 'posts'
    if not q and not flair:
        return {"q": "", "_flair": "", "_stype": stype}
    after = request.args.get('after', '')

    if stype == 'communities':
        return _try_inject_search_communities(q, after)
    if stype == 'users':
        return _try_inject_search_users(q, after)

    sort = request.args.get('sort', 'relevance')
    if sort not in SEARCH_SORTS:
        sort = 'relevance'
    t     = request.args.get('t', 'all')
    sub   = request.args.get('sub', '').strip()
    query_text = f'{q} flair:"{flair}"'.strip() if flair else q
    try:
        url = f"https://www.reddit.com/r/{sub}/search.json" if sub else "https://www.reddit.com/search.json"
        params = {"q": query_text, "sort": sort, "t": t, "limit": FEED_LIMIT, "raw_json": 1}
        if sub:
            params["restrict_sr"] = 1
        if after:
            params["after"] = after
        r = reddit_get(url, params=params, timeout=6)
        if r.status_code != 200:
            return {"q": q, "_flair": flair, "_stype": stype, "error": f"Reddit returned {r.status_code}"}
        listing = r.json()["data"]
        next_after = listing.get("after")
        result = {"q": q, "_flair": flair, "_stype": stype, "posts": extract_posts(listing), "after": next_after,
                  "_sort": sort, "_t": t, "_sub": sub}
        if next_after:
            result["_next_url"] = _ns_url("/search", q=q, flair=flair, stype=stype, sort=sort if sort != 'relevance' else '',
                                           t=t if t != 'all' else '', sub=sub, after=next_after)
        return result
    except Exception as e:
        log.warning("inject search q=%r: %s", q, e)
        return {"q": q, "_flair": flair, "_stype": stype, "error": "Search failed"}


def _try_inject_search_communities(q, after=''):
    try:
        params = {"q": q, "limit": FEED_LIMIT, "raw_json": 1, "type": "sr"}
        if after:
            params["after"] = after
        r = reddit_get("https://www.reddit.com/search.json", params=params, timeout=6)
        if r.status_code != 200:
            return {"q": q, "_stype": "communities", "error": f"Reddit returned {r.status_code}"}
        listing = r.json()["data"]
        communities = []
        for c in listing["children"]:
            if c.get("kind") != "t5":
                continue
            d = c["data"]
            icon = clean_url(d.get("icon_img") or d.get("community_icon") or "")
            communities.append({"name": d.get("display_name", ""), "title": d.get("title", ""),
                                 "description": d.get("public_description", ""),
                                 "subscribers": d.get("subscribers", 0), "icon": icon or ""})
        next_after = listing.get("after")
        result = {"q": q, "_stype": "communities", "communities": communities, "after": next_after}
        if next_after:
            result["_next_url"] = _ns_url("/search", q=q, stype="communities", after=next_after)
        return result
    except Exception as e:
        log.warning("inject search communities q=%r: %s", q, e)
        return {"q": q, "_stype": "communities", "error": "Search failed"}


def _try_inject_search_users(q, after=''):
    try:
        params = {"q": q, "limit": FEED_LIMIT, "raw_json": 1, "type": "user"}
        if after:
            params["after"] = after
        r = reddit_get("https://www.reddit.com/search.json", params=params, timeout=6)
        if r.status_code != 200:
            return {"q": q, "_stype": "users", "error": f"Reddit returned {r.status_code}"}
        listing = r.json()["data"]
        users = []
        for c in listing["children"]:
            if c.get("kind") != "t2":
                continue
            d = c["data"]
            users.append({"name": d.get("name", ""), "karma_post": d.get("link_karma", 0),
                          "karma_comment": d.get("comment_karma", 0), "created_utc": d.get("created_utc", 0)})
        next_after = listing.get("after")
        result = {"q": q, "_stype": "users", "users": users, "after": next_after}
        if next_after:
            result["_next_url"] = _ns_url("/search", q=q, stype="users", after=next_after)
        return result
    except Exception as e:
        log.warning("inject search users q=%r: %s", q, e)
        return {"q": q, "_stype": "users", "error": "Search failed"}


def _try_inject_duplicates(sub, post_id):
    """Fetch a post's crosspost/duplicate listing for SSR injection."""
    try:
        after = request.args.get('after', '')
        params = {"raw_json": 1, "limit": 25}
        if after:
            params["after"] = after
        r = reddit_get(f"https://old.reddit.com/r/{sub}/duplicates/{post_id}.json", params=params, timeout=6)
        if r.status_code != 200:
            return None
        data = r.json()
        orig_children = data[0]["data"]["children"]
        post = process_post(orig_children[0]["data"]) if orig_children else None
        listing = data[1]["data"]
        next_after = listing.get("after")
        result = {"post": post, "posts": extract_posts(listing), "after": next_after,
                  "_sub": sub.lower(), "_post_id": post_id}
        if next_after:
            result["_next_url"] = _ns_url(f"/r/{sub.lower()}/duplicates/{post_id}", after=next_after)
        return result
    except Exception as e:
        log.warning("inject duplicates sub=%s post=%s: %s", sub, post_id, e)
        return None


_WIKI_PAGE_RE = re.compile(r'^[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*$')


def _try_inject_wiki(sub, page):
    page = page or 'index'
    if not _WIKI_PAGE_RE.match(page):
        return {"error": "Invalid page name"}
    try:
        r = reddit_get(f"https://www.reddit.com/r/{sub}/wiki/{page}.json", params={"raw_json": 1}, timeout=6)
        if r.status_code == 404:
            return {"error": "Wiki page not found"}
        if r.status_code == 403:
            return {"error": "Wiki is private or disabled"}
        if r.status_code != 200:
            return {"error": f"Reddit returned {r.status_code}"}
        d = r.json()["data"]
        return {"html": clean_reddit_html(d.get("content_html")), "_sub": sub.lower(), "_page": page}
    except Exception as e:
        log.warning("inject wiki sub=%s page=%s: %s", sub, page, e)
        return {"error": "Failed to load wiki page"}


@bp.route("/r/<path:reddit_path>")
def r_json_or_spa(reddit_path):
    if reddit_path.endswith(".json"):
        if not _JSON_PASSTHROUGH_RE.match(f"r/{reddit_path}"):
            return jsonify({"error": "not found"}), 404
        return _proxy_reddit(f"r/{reddit_path}")
    initial_data = initial_about = initial_post = None
    m = _SUB_FEED_RE.match(reddit_path)
    if m:
        sub   = m.group(1)
        sort  = m.group(2) or ('hot' if sub.lower() == 'popular' else 'top')
        time  = request.args.get('t', 'all')
        after = request.args.get('after', '')
        initial_data, initial_about = _try_inject_subreddit(sub, sort, time, after)
    else:
        mp = _POST_PERMALINK_RE.match(reddit_path)
        if mp:
            sub, post_id, comment_id = mp.group(1), mp.group(2), mp.group(3)
            comment_sort = request.args.get('sort', 'confidence')
            if comment_sort not in COMMENT_SORTS:
                comment_sort = 'confidence'
            try:
                data, err = _fetch_comments_data(sub, post_id, comment_id, sort=comment_sort, timeout=6)
                if not err:
                    data["_sub"] = sub.lower()
                    data["_post_id"] = post_id
                    data["_comment_id"] = comment_id or ''
                    data["_comment_sort"] = comment_sort
                    base = f"/r/{sub.lower()}/comments/{post_id}"
                    data["_comment_sort_links"] = {s: _ns_url(base, sort=s if s != 'confidence' else '')
                                                    for s in sorted(COMMENT_SORTS)}
                    initial_post = data
            except Exception as e:
                log.warning("inject post sub=%s post=%s: %s", sub, post_id, e)
    resp = render_template("index.html", initial_data=initial_data, initial_about=initial_about, initial_post=initial_post,
                           disable_downloads=DISABLE_DOWNLOADS, ns_hls=_ns_hls_enabled())
    return resp, 200, {'Cache-Control': 'no-store'}


def _proxy_reddit(reddit_path):
    url = f"https://oauth.reddit.com/{reddit_path}"
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8")
    try:
        resp = reddit_get(url, timeout=15)
    except Exception as e:
        log.warning("proxy request failed path=%s: %s", reddit_path, e)
        return jsonify({"error": "upstream request failed"}), 502
    content_type = resp.headers.get("Content-Type", "application/json")
    return Response(resp.content, status=resp.status_code, content_type=content_type)


@bp.route("/<path:reddit_path>")
def json_catch_all(reddit_path):
    if reddit_path.endswith(".json") and _JSON_PASSTHROUGH_RE.match(reddit_path):
        return _proxy_reddit(reddit_path)
    return jsonify({"error": "not found"}), 404
