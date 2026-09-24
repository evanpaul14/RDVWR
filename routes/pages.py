"""HTML page routes: the SPA shell with server-fetched data for hydration and the
<noscript> fallback (built in routes/page_data.py)."""
import re
from flask import Blueprint, jsonify, redirect, render_template, request
from helpers import (DISABLE_DOWNLOADS, FEED_SORTS, COMMENT_SORTS, SEARCH_SORTS, SUBREDDIT_RE,
                     USERNAME_RE, POST_ID_RE, MULTINAME_RE, log)
from reddit_html import local_reddit_path
from ns_prefs import get_pref
from routes.live import LIVE_ID_RE
from routes.media import resolve_reddit_url
from routes.page_data import (build_home, build_subreddit, build_multi, build_duplicates, build_wiki,
                              build_post, build_profile, build_search, build_live, build_message,
                              clean_time, TIMED_SORTS)

bp = Blueprint("pages", __name__)

_TOKEN_RE = re.compile(r'^[A-Za-z0-9]{1,20}$')
_SEARCH_SHORTCUT_RE = re.compile(
    r'^/?(?:r/(?P<sub>[A-Za-z0-9_+]+)|u(?:ser)?/(?P<user>[A-Za-z0-9_-]+)(?:/m/(?P<multi>[A-Za-z0-9_]+))?)/?$', re.I)


def respond(ctx, status=200):
    if "redirect" in ctx:
        return redirect(ctx["redirect"])
    html = render_template("index.html", disable_downloads=DISABLE_DOWNLOADS, **ctx)
    return html, status, {'Cache-Control': 'no-store'}


def _not_found(message="There's nothing at this address."):
    return respond(build_message("Page not found", message, "/", "Go home"), 404)


def _arg(name, default=''):
    return request.args.get(name, default).strip()


def _feed_sort(sort, default):
    return sort if sort in FEED_SORTS else default


# ── Feeds ─────────────────────────────────────────────────────────────────────

@bp.route("/", strict_slashes=False)
@bp.route("/home", strict_slashes=False)
@bp.route("/home/<sort>", strict_slashes=False)
def home(sort='best'):
    return respond(build_home(_feed_sort(sort, 'best'), clean_time(_arg('t'), 'all'), _arg('after')))


@bp.route("/r/<sub>", strict_slashes=False)
@bp.route("/r/<sub>/<sort>", strict_slashes=False)
def subreddit(sub, sort=''):
    if not SUBREDDIT_RE.match(sub):
        return _not_found()
    default_sort = 'hot' if sub.lower() == 'popular' else get_pref('sub_sort')
    return respond(build_subreddit(sub, _feed_sort(sort, default_sort),
                                    clean_time(_arg('t'), get_pref('sub_time')), _arg('after'),
                                    bool(_arg('quarantine_opt_in'))))


@bp.route("/r/<sub>/<path:rest>")
def subreddit_subpage(sub, rest):
    """Any other /r/<sub>/... path: the JS router treats it as the subreddit itself."""
    return subreddit(sub)


@bp.route("/user/<username>/m/<multiname>", strict_slashes=False)
@bp.route("/user/<username>/m/<multiname>/<sort>", strict_slashes=False)
@bp.route("/u/<username>/m/<multiname>", strict_slashes=False)
@bp.route("/u/<username>/m/<multiname>/<sort>", strict_slashes=False)
def multireddit(username, multiname, sort='hot'):
    if not USERNAME_RE.match(username) or not MULTINAME_RE.match(multiname):
        return _not_found()
    sort = _feed_sort(sort, 'hot')
    t = clean_time(_arg('t'), 'day' if sort in TIMED_SORTS else 'all')
    return respond(build_multi(username, multiname, sort, t, _arg('after')))


# ── Posts ─────────────────────────────────────────────────────────────────────

@bp.route("/r/<sub>/comments/<post_id>", strict_slashes=False)
@bp.route("/r/<sub>/comments/<post_id>/<slug>", strict_slashes=False)
@bp.route("/r/<sub>/comments/<post_id>/<slug>/<comment_id>", strict_slashes=False)
def post(sub, post_id, slug='', comment_id=''):
    if not SUBREDDIT_RE.match(sub) or not POST_ID_RE.match(post_id) \
            or (comment_id and not POST_ID_RE.match(comment_id)):
        return _not_found()
    sort = _arg('sort')
    if sort not in COMMENT_SORTS:
        sort = get_pref('comment_sort')
    more = ','.join(i for i in _arg('more').split(',') if POST_ID_RE.match(i))
    return respond(build_post(sub, post_id, comment_id, sort, _arg('context') == '1', more))


@bp.route("/r/<sub>/duplicates/<post_id>", strict_slashes=False)
def duplicates(sub, post_id):
    if not SUBREDDIT_RE.match(sub) or not POST_ID_RE.match(post_id):
        return _not_found()
    return respond(build_duplicates(sub, post_id, _arg('after')))


@bp.route("/r/<sub>/wiki", strict_slashes=False)
@bp.route("/r/<sub>/wiki/<path:page>", strict_slashes=False)
def wiki(sub, page='index'):
    if not SUBREDDIT_RE.match(sub):
        return _not_found()
    return respond(build_wiki(sub, page.strip('/') or 'index'))


@bp.route("/r/<sub>/s/<token>", strict_slashes=False)
def share_link(sub, token):
    """Resolve a Reddit share link and redirect to this site's copy of its target."""
    if not SUBREDDIT_RE.match(sub) or not _TOKEN_RE.match(token):
        return _not_found()
    reddit_url = f"https://www.reddit.com/r/{sub}/s/{token}"
    try:
        target = resolve_reddit_url(reddit_url)
    except Exception as e:
        log.warning("share link resolve failed sub=%s: %s", sub, e)
        target = None
    local = target and local_reddit_path(target)
    if local and '/s/' not in local:
        return redirect(local)
    return respond(build_message("Shared link", "Couldn't work out where this shared link goes.",
                                  target or reddit_url, "Open it on reddit.com"))


# ── Users, search, live ───────────────────────────────────────────────────────

@bp.route("/user/<username>", strict_slashes=False)
@bp.route("/u/<username>", strict_slashes=False)
@bp.route("/user/<username>/<path:rest>")
@bp.route("/u/<username>/<path:rest>")
def profile(username, rest=''):
    if not USERNAME_RE.match(username):
        return _not_found()
    return respond(build_profile(username, _arg('tab', 'overview'), _arg('sort', 'new'),
                                  clean_time(_arg('t'), 'all'), _arg('after'), _arg('archive') == '1'))


def _search_shortcut(q):
    """Path for an "r/<sub>", "u/<user>" or "u/<user>/m/<multi>" query, else None."""
    m = _SEARCH_SHORTCUT_RE.match(q)
    if not m:
        return None
    if m.group('sub'):
        return f"/r/{m.group('sub')}"
    if m.group('multi'):
        return f"/user/{m.group('user')}/m/{m.group('multi')}"
    return f"/user/{m.group('user')}"


@bp.route("/search", strict_slashes=False)
def search():
    q = _arg('q')
    shortcut = _search_shortcut(q)
    if shortcut:
        return redirect(shortcut)
    sort = _arg('sort', 'relevance')
    sub = _arg('sub')
    scope = _arg('scope')
    return respond(build_search(
        q, _arg('stype', 'posts'), sort if sort in SEARCH_SORTS else 'relevance',
        clean_time(_arg('t'), 'all'), sub if SUBREDDIT_RE.match(sub) else '',
        scope if SUBREDDIT_RE.match(scope) else '', _arg('after')))


@bp.route("/live/<thread_id>", strict_slashes=False)
def live(thread_id):
    if not LIVE_ID_RE.match(thread_id):
        return _not_found()
    return respond(build_live(thread_id, _arg('after')))


# ── Pages with no no-JS version ───────────────────────────────────────────────

@bp.route("/saved", strict_slashes=False)
def saved():
    return respond(build_message(
        "Saved", "Saved posts are kept in your browser's storage, which needs JavaScript."))


@bp.route("/subscribed", strict_slashes=False)
@bp.route("/subscribed/<sort>", strict_slashes=False)
def subscribed(sort=''):
    return respond(build_message(
        "Subscribed", "Your subscriptions are kept in your browser's storage, which needs JavaScript."))


@bp.route("/<path:path>")
def unknown(path):
    if path.startswith('api/'):
        return jsonify({"error": "not found"}), 404
    return _not_found()
