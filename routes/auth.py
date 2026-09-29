"""Reddit account login (see reddit_login.py). Every route 404s unless REDDIT_LOGIN=1, and
only answers requests that come straight from the machine running the server: the login
belongs to whoever owns the instance, so a public visitor (or anyone behind a reverse
proxy) can't sign in or out."""
from flask import Blueprint, abort, redirect, request
from helpers import is_same_site_request, safe_next
import reddit_login

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


@bp.route("/auth/reddit/logout", methods=["POST"])
def logout():
    _require_local_post()
    reddit_login.logout()
    return redirect(safe_next(request.form.get('next')))
