"""Reddit account login (see reddit_login.py). Every route 404s unless REDDIT_LOGIN=1, and
only answers requests that come straight from the machine running the server: the login
belongs to whoever owns the instance, so a public visitor (or anyone behind a reverse
proxy) can't sign in or out. The exception is a browser holding the owner cookie
(reddit_owner.py, REDDIT_LOGIN_OWNER_KEY)."""
from flask import Blueprint, abort, redirect, request
from markupsafe import escape
from helpers import is_same_site_request, safe_next
import reddit_login
import reddit_oauth
import reddit_owner

bp = Blueprint("auth", __name__)


def _require_local_post():
    if not reddit_login.ENABLED or not reddit_login.is_local_request():
        abort(404)
    if not is_same_site_request():
        abort(403)


@bp.route("/auth/reddit/login", methods=["POST"])
def login():
    _require_local_post()
    try:
        reddit_login.login_with_cookies(request.form.get('cookies', ''))
    except Exception as e:
        return f"Login failed: {e}", 400
    return redirect(safe_next(request.form.get('next')))


@bp.route("/auth/reddit/oauth/start")
def oauth_start():
    if not reddit_login.ENABLED or not reddit_login.is_local_request():
        abort(404)
    return redirect(reddit_oauth.authorize_url())


@bp.route("/auth/reddit/oauth/finish", methods=["POST"])
def oauth_finish():
    _require_local_post()
    try:
        reddit_login.login_with_oauth(request.form.get('pasted', ''))
    except Exception as e:
        return f"Login failed: {e}", 400
    return redirect(safe_next(request.form.get('next')))


@bp.route("/auth/reddit/logout", methods=["POST"])
def logout():
    _require_local_post()
    reddit_login.logout()
    return redirect(safe_next(request.form.get('next')))


_OWNER_PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Owner sign-in</title><body style="font:16px system-ui;background:#111;color:#ddd;max-width:24rem;margin:4rem auto;padding:0 1rem">
<h1 style="font-size:1.2rem">Owner sign-in</h1>%s
<form method=post><input type=password name=key placeholder="Owner key" autofocus required style="width:100%%;padding:.5rem;margin:.5rem 0">
<input type=hidden name=next value="%s"><button style="padding:.5rem 1rem">Sign in</button></form>
<form method=post action="/auth/owner/logout" style="margin-top:1rem"><button style="padding:.4rem .8rem">Forget this browser</button></form>"""


@bp.route("/auth/owner", methods=["GET", "POST"])
def owner():
    if not reddit_login.ENABLED or not reddit_owner.KEY:
        abort(404)
    nxt = safe_next(request.values.get('next'))
    if request.method == "GET":
        note = "<p>This browser is signed in as owner.</p>" if reddit_owner.is_owner() else ""
        return _OWNER_PAGE % (note, escape(nxt))
    if not is_same_site_request():
        abort(403)
    if not reddit_owner.check_key(request.form.get('key', '')):
        return _OWNER_PAGE % ("<p>Wrong key, or too many attempts. Try again later.</p>", escape(nxt)), 403
    resp = redirect(nxt)
    resp.set_cookie(reddit_owner.COOKIE, reddit_owner.make_token(), max_age=reddit_owner.MAX_AGE,
                    httponly=True, samesite='Lax',
                    secure=request.headers.get('X-Forwarded-Proto') == 'https' or request.is_secure)
    return resp


@bp.route("/auth/owner/logout", methods=["POST"])
def owner_logout():
    if not reddit_login.ENABLED or not reddit_owner.KEY:
        abort(404)
    if not is_same_site_request():
        abort(403)
    resp = redirect("/")
    resp.delete_cookie(reddit_owner.COOKIE)
    return resp
