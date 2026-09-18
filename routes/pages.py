"""SPA page routes (with server-side data injection) and the raw .json Reddit passthrough."""
import re
from flask import Blueprint, jsonify, request, render_template, Response
from media_detection import extract_posts, clean_url
from reddit_client import reddit_get
from helpers import FEED_LIMIT, add_time_param, parallel, log
from routes.users import _fetch_user_about, _fetch_user_overview
from routes.comments import _fetch_comments_data

bp = Blueprint("pages", __name__)


_SUB_FEED_RE = re.compile(r'^([A-Za-z0-9_]+)(?:/(hot|new|top|rising|controversial))?$')
_POST_PERMALINK_RE = re.compile(r'^([A-Za-z0-9_]+)/comments/([A-Za-z0-9]+)(?:/[^/]*(?:/([A-Za-z0-9]+))?)?/?$')


# ── SPA catch-all routes ──────────────────────────────────────────────────────

@bp.route("/", strict_slashes=False)
@bp.route("/home", strict_slashes=False)
@bp.route("/home/<sort>", strict_slashes=False)
@bp.route("/user/<username>", strict_slashes=False)
@bp.route("/user/<username>/m/<multiname>", strict_slashes=False)
@bp.route("/user/<username>/m/<multiname>/<path:rest>", strict_slashes=False)
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
    initial_profile = None
    username = kwargs.get('username')
    if username and 'multiname' not in kwargs:
        initial_profile = _try_inject_profile(username)
    resp = render_template("index.html", initial_profile=initial_profile)
    return resp, 200, {'Cache-Control': 'no-store'}


def _try_inject_profile(username):
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
            data, err = _fetch_user_overview(username, timeout=6, allow_archive=False)
            return None if err else data
        except Exception as e:
            log.warning("inject profile overview user=%s: %s", username, e)
            return None

    about, overview = parallel(_about, _overview)
    if overview is None:
        return None
    overview["_username"] = username.lower()
    overview["_about"] = about
    return overview


def _try_inject_subreddit(sub, sort, time):
    """Fetch subreddit feed + about in parallel for SSR injection.
    Returns (feed_dict, about_dict); either may be None on error."""
    def _feed():
        try:
            url = f"https://www.reddit.com/r/{sub.replace('+', '%2B')}/{sort}.json"
            params = {"limit": FEED_LIMIT, "raw_json": 1}
            add_time_param(params, sort, time)
            r = reddit_get(url, params=params, timeout=6)
            if r.status_code != 200:
                return None
            listing = r.json()["data"]
            return {"posts": extract_posts(listing), "after": listing.get("after"),
                    "_sub": sub.lower(), "_sort": sort, "_time": time}
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
            return {"title": d.get("title", sub), "description": d.get("public_description", ""),
                    "subscribers": d.get("subscribers", 0), "active": active,
                    "icon": icon or "", "_sub": sub.lower()}
        except Exception as e:
            log.warning("inject about sub=%s: %s", sub, e)
            return None

    return parallel(_feed, _about)


@bp.route("/r/<path:reddit_path>")
def r_json_or_spa(reddit_path):
    if reddit_path.endswith(".json"):
        return _proxy_reddit(f"r/{reddit_path}")
    initial_data = initial_about = initial_post = None
    m = _SUB_FEED_RE.match(reddit_path)
    if m:
        sub  = m.group(1)
        sort = m.group(2) or ('hot' if sub.lower() == 'popular' else 'top')
        time = request.args.get('t', 'all')
        initial_data, initial_about = _try_inject_subreddit(sub, sort, time)
    else:
        mp = _POST_PERMALINK_RE.match(reddit_path)
        if mp:
            sub, post_id, comment_id = mp.group(1), mp.group(2), mp.group(3)
            try:
                data, err = _fetch_comments_data(sub, post_id, comment_id, timeout=6)
                if not err:
                    data["_sub"] = sub.lower()
                    data["_post_id"] = post_id
                    data["_comment_id"] = comment_id or ''
                    initial_post = data
            except Exception as e:
                log.warning("inject post sub=%s post=%s: %s", sub, post_id, e)
    resp = render_template("index.html", initial_data=initial_data, initial_about=initial_about, initial_post=initial_post)
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
    if reddit_path.endswith(".json"):
        return _proxy_reddit(reddit_path)
    return jsonify({"error": "not found"}), 404
