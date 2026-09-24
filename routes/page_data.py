"""Template context for routes/pages.py. Each build_* returns either {"redirect": url}
or a dict with:

- `initial_*`: JS hydration data (window.__INITIAL_*__); its `_`-prefixed keys must
  match what feed.js/postview.js/profile.js check.
- `ns_view` + `page`: the templates/noscript/<ns_view>.html partial and its data.
"""
from urllib.parse import urlencode
from helpers import TIME_FILTERS, UpstreamError, parallel, log
from ns_prefs import get_pref
from routes.home import fetch_frontpage
from routes.subreddit import (fetch_feed, fetch_about, fetch_rules, fetch_moderators, fetch_widgets,
                              fetch_duplicates, fetch_wiki, fetch_multireddit)
from routes.comments import fetch_comments, fetch_morechildren
from routes.users import fetch_user_about, fetch_user_overview, fetch_user_posts, fetch_user_comments
from routes.search import fetch_search_posts, fetch_search_communities, fetch_search_users
from routes.live import fetch_live_thread
from reddit_html import sanitize_reddit_html

SSR_TIMEOUT = 6  # shorter than /api/* so a page doesn't hang on Reddit

FEED_SORT_ORDER = ('best', 'hot', 'new', 'top', 'rising', 'controversial')
TIMED_SORTS     = ('top', 'controversial')
# Pseudo-subreddits with no about/rules/moderators/wiki of their own.
LISTING_SUBS    = ('popular', 'all')

PROFILE_TABS  = ('overview', 'posts', 'comments')
PROFILE_SORTS = {'overview': ('hot', 'new', 'top'), 'posts': ('hot', 'new', 'top'), 'comments': ('new', 'top')}
SEARCH_TYPES  = ('posts', 'communities', 'users')


def page_url(path, **params):
    """A same-site URL with the given query params; falsy params are left out."""
    qs = {k: v for k, v in params.items() if v}
    return path + ('?' + urlencode(qs) if qs else '')


def _attempt(fetch, *args, **kwargs):
    """(data, None) or (None, UpstreamError), so a page can render its error state."""
    try:
        return fetch(*args, timeout=SSR_TIMEOUT, **kwargs), None
    except UpstreamError as e:
        return None, e
    except Exception as e:
        log.warning("page fetch %s%r failed: %s", fetch.__name__, args, e)
        return None, UpstreamError("Couldn't reach Reddit — try again in a moment")


def _page(view, title, page, **hydration):
    return {"ns_view": view, "page_title": f"{title} — RDVWR" if title else "RDVWR",
            "page": page, **hydration}


def _feed_nav(base, sort, t, next_after, **extra):
    """Sort bar, time filter and next-page link for a paginated post listing at base/<sort>."""
    timed = sort in TIMED_SORTS
    return {
        "base": base, "sorts": FEED_SORT_ORDER, "sort": sort, "time": t if timed else None,
        "next_url": page_url(f"{base}/{sort}", t=t if timed else '', after=next_after, **extra)
                    if next_after else None,
    }


def clean_time(t, default):
    return t if t in TIME_FILTERS else default


# ── Feeds ─────────────────────────────────────────────────────────────────────

def build_home(sort, t, after):
    """The logged-out front page (the personalized feed needs cookies only JS sends)."""
    feed, err = _attempt(fetch_frontpage, sort, t, after)
    return _page("home", "Home", {
        "nav": _feed_nav("/home", sort, t, feed and feed["after"]),
        "posts": feed["posts"] if feed else [], "error": err,
    })


def build_subreddit(sub, sort, t, after, quarantine_opt_in=False):
    key = sub.lower()
    single = '+' not in sub
    real_sub = single and key not in LISTING_SUBS

    def optional(fetch, default):
        return (lambda: fetch(sub, timeout=SSR_TIMEOUT)) if real_sub else (lambda: default)

    (feed, err), (about, _), rules, mods, widgets = parallel(
        lambda: _attempt(fetch_feed, sub, sort, t, after, quarantine_opt_in),
        lambda: _attempt(fetch_about, sub) if single else (None, None),
        optional(fetch_rules, []), optional(fetch_moderators, []), optional(fetch_widgets, []))

    if err and err.state == 'not_found':
        # Same as the JS app: an unknown subreddit name becomes a community search.
        return {"redirect": page_url("/search", q=sub, stype="communities")}

    hydration = {}
    if about is not None:
        about["_sub"] = key
        hydration["initial_about"] = about
    if feed is not None:
        hydration["initial_data"] = {**feed, "_sub": key, "_sort": sort, "_time": t}

    opt_in = 1 if quarantine_opt_in else ''
    return _page("subreddit", f"r/{sub}", {
        "sub": sub, "about": about, "rules": rules, "mods": mods, "widgets": widgets,
        "nav": _feed_nav(f"/r/{sub}", sort, t, feed and feed["after"], quarantine_opt_in=opt_in),
        "posts": feed["posts"] if feed else [], "error": err,
        "continue_url": page_url(f"/r/{sub}/{sort}", t=t if sort in TIMED_SORTS else '', quarantine_opt_in=1)
                        if err and err.state == 'quarantined' else None,
        "show_wiki": real_sub,
    }, scope_sub=sub if real_sub else None, **hydration)


def build_multi(username, multiname, sort, t, after):
    feed, err = _attempt(fetch_multireddit, username, multiname, sort, t, after)
    title = feed["title"] if feed else multiname
    return _page("multi", title, {
        "username": username, "title": title,
        "nav": _feed_nav(f"/user/{username}/m/{multiname}", sort, t, feed and feed["after"]),
        "posts": feed["posts"] if feed else [], "error": err,
    })


def build_duplicates(sub, post_id, after):
    data, err = _attempt(fetch_duplicates, sub, post_id, after)
    post = data and data["post"]
    return _page("duplicates", f"Duplicates: {post['title']}" if post else "Duplicates", {
        "sub": sub, "post_id": post_id, "post": post, "error": err,
        "posts": data["posts"] if data else [],
        "next_url": page_url(f"/r/{sub}/duplicates/{post_id}", after=data["after"])
                    if data and data["after"] else None,
    })


def build_wiki(sub, page):
    data, err = _attempt(fetch_wiki, sub, page)
    return _page("wiki", f"{page} — {sub} wiki", {
        "sub": sub, "page": page, "error": err,
        "html": sanitize_reddit_html(data["content_html"]) if data else '',
    }, scope_sub=sub)


# ── Post + comments ───────────────────────────────────────────────────────────

def _find_comment(comments, comment_id):
    for c in comments:
        if c.get("id") == comment_id:
            return c
        found = _find_comment(c.get("replies") or [], comment_id)
        if found:
            return found
    return None


def build_post(sub, post_id, comment_id='', sort='confidence', show_context=False, more_ids=''):
    """A post and its comments. `comment_id` shows one thread (show_context adds its
    parents); `more_ids` shows the comments behind a "load more" stub on their own page."""
    if more_ids:
        (data, err), (more, more_err) = parallel(
            lambda: _attempt(fetch_comments, sub, post_id, sort=sort),
            lambda: _attempt(fetch_morechildren, post_id, more_ids, sort))
        err = err or more_err
    else:
        data, err = _attempt(fetch_comments, sub, post_id, comment_id or None, sort=sort)
    if err:
        return _page("post", "Post", {"error": err, "sub": sub})

    post = data["post"]
    comments = data["comments"]
    thread_link = None
    base = f"/r/{sub}/comments/{post_id}"
    if more_ids:
        comments = more["comments"]
        thread_link = page_url(base, sort=sort)
    elif comment_id:
        is_top_level = any(c.get("id") == comment_id for c in comments)
        if show_context or is_top_level:
            thread_link = page_url(base, sort=sort)
        else:
            thread_link = page_url(f"{base}/_/{comment_id}", sort=sort, context=1)
            target = _find_comment(comments, comment_id)
            comments = [target] if target else comments

    thread_path = f"{base}/_/{comment_id}" if comment_id else base
    hydration = {}
    if sort == 'confidence':
        hydration["initial_post"] = {**data, "_sub": sub.lower(), "_post_id": post_id,
                                     "_comment_id": comment_id or ''}
    return _page("post", post["title"], {
        "post": post, "comments": comments, "comment_sort": sort, "thread_link": thread_link,
        "sort_links": {s: page_url(thread_path, sort=s, context=1 if show_context else '', more=more_ids)
                       for s in ('confidence', 'top', 'new', 'controversial', 'old', 'qa')},
    }, scope_sub=sub, **hydration)


# ── Users ─────────────────────────────────────────────────────────────────────

def build_profile(username, tab='overview', sort='new', t='all', after='', archive=False):
    """A user profile; profile.js's tabs/sorts become query params here."""
    if tab not in PROFILE_TABS:
        tab = 'overview'
    if sort not in PROFILE_SORTS[tab]:
        sort = 'new'
    t = t if sort == 'top' else ''

    def listing():
        if tab == 'posts':
            return _attempt(fetch_user_posts, username, sort, t, after)
        if tab == 'comments':
            return _attempt(fetch_user_comments, username, sort, t, after)
        # The archive fallback is slow, so page loads only use it when asked (`archive`).
        return _attempt(fetch_user_overview, username, sort, t, after,
                        allow_archive=archive or after.startswith("arc:"))

    (data, err), (about, _) = parallel(listing, lambda: _attempt(fetch_user_about, username))

    items = []
    if data:
        if tab == 'posts':
            items = [{"type": "post", "data": p} for p in data["posts"]]
        elif tab == 'comments':
            items = [{"type": "comment", "data": c} for c in data["comments"]]
        else:
            items = data["items"]

    hydration = {}
    if data and tab == 'overview' and sort == 'new' and not t:
        hydration["initial_profile"] = {**data, "_username": username.lower(), "_about": about}

    base = f"/user/{username}"

    def url(tab_, sort_='', **extra):
        return page_url(base, tab=tab_ if tab_ != 'overview' else '', sort=sort_ if sort_ != 'new' else '', **extra)

    return _page("profile", f"u/{username}", {
        "username": username, "about": about, "tab": tab, "sort": sort, "time": t or 'all', "base": base,
        "tab_links": [(tab_, url(tab_)) for tab_ in PROFILE_TABS],
        "sort_links": [(sort_, url(tab, sort_)) for sort_ in PROFILE_SORTS[tab]],
        "items": items, "archived": bool(data and data.get("archived")), "error": err,
        "archive_url": url('overview', archive=1) if err and tab == 'overview' and not archive else None,
        "next_url": url(tab, sort, t=t, after=data["after"]) if data and data.get("after") else None,
    }, **hydration)


# ── Search ────────────────────────────────────────────────────────────────────

def build_search(q, stype='posts', sort='relevance', t='all', sub='', scope='', after=''):
    """Search results. `sub` restricts to one subreddit; `scope` remembers which one
    the "r/<sub>" toggle controls while it's unticked."""
    scope = scope or sub
    scoped = bool(sub) or 'flair:' in q
    if scoped or stype not in SEARCH_TYPES:
        stype = 'posts'   # the JS app hides the type tabs for scoped searches, too
    if sort != 'top':
        t = 'all'         # only "top" offers a time filter

    def url(**overrides):
        params = {"q": q, "stype": stype, "sort": sort, "t": t, "sub": sub, "scope": scope, **overrides}
        return page_url("/search", q=params["q"],
                        stype=params["stype"] if params["stype"] != 'posts' else '',
                        sort=params["sort"] if params["sort"] != 'relevance' else '',
                        t=params["t"] if params["t"] != 'all' else '', sub=params["sub"],
                        scope=params["scope"] if params["scope"] != params["sub"] else '',
                        after=params.get("after", ''))

    page = {
        "q": q, "stype": stype, "sort": sort, "time": t, "sub": sub, "scope": scope, "scoped": scoped,
        "type_links": [(s, url(stype=s, sort='relevance', t='all')) for s in SEARCH_TYPES],
        "sort_links": [(s, url(sort=s, t='all')) for s in ('relevance', 'hot', 'top', 'new')],
        # The JS app's "only r/<sub>" checkbox: toggling it re-runs the search.
        "scope_toggle_url": url(sub='' if sub else scope) if scope else None,
        "time_form_fields": {"q": q, "sort": sort, "sub": sub, "scope": scope if scope != sub else ''},
        "results": [], "error": None, "next_url": None,
    }
    if not q:
        return _page("search", "Search", page)

    if stype == 'communities':
        data, err = _attempt(fetch_search_communities, q, after)
    elif stype == 'users':
        data, err = _attempt(fetch_search_users, q, after)
    else:
        nsfw = not (get_pref('nsfw_hide') or get_pref('nsfw_search_hide'))
        data, err = _attempt(fetch_search_posts, q, sort, t, sub, nsfw, after)
    page.update(results=data[stype] if data else [], error=err,
                next_url=url(after=data["after"]) if data and data.get("after") else None)
    return _page("search", f"Search: {q}" + (f" in r/{sub}" if sub else ""), page, scope_sub=sub or None)


# ── Everything else ───────────────────────────────────────────────────────────

def build_live(thread_id, after=''):
    data, err = _attempt(fetch_live_thread, thread_id, after)
    return _page("live", f"{data['title']} — LIVE" if data else "Live thread", {
        "thread_id": thread_id, "thread": data, "error": err, "is_first_page": not after,
        "next_url": page_url(f"/live/{thread_id}", after=data["after"]) if data and data["after"] else None,
    })


def build_message(title, message, link=None, link_label=None):
    """A page that's just a message (no no-JS version, or not found)."""
    return _page("message", title, {"title": title, "message": message,
                                    "link": link, "link_label": link_label})
