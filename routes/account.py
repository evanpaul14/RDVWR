"""Managing the logged-in Reddit account's own profile: its posts and comments (edit, delete)
and its profile picture. Signing out is /auth/reddit/logout (routes/auth.py). Same local-only
rules as routes/actions.py; /api/* is for the JS app, /actions/account/* are the no-JS forms."""
import re
from flask import Blueprint, jsonify, redirect, request
from helpers import UpstreamError, safe_next
from reddit_client import reddit_get
import reddit_actions
import reddit_login
from routes.actions import require_account
from routes.users import fetch_user_about, fetch_user_posts, fetch_user_comments

bp = Blueprint("account", __name__)

_FULLNAME_RE = re.compile(r'^t[13]_[a-z0-9]{1,12}$')
_TEXT_MAX = {'t1': 10000, 't3': 40000}
_AVATAR_TYPES = ('image/jpeg', 'image/png')
_AVATAR_MAX = 5 * 1024 * 1024
_NO_STORE = {'Cache-Control': 'private, no-store'}


def fetch_full_texts(fullnames, timeout=10):
    """{fullname: raw markdown} for the account's own posts/comments. Listings cut post text
    short, so anything that pre-fills an edit box needs this to avoid saving a truncated copy."""
    resp = reddit_get("https://www.reddit.com/api/info.json", params={"id": ",".join(fullnames), "raw_json": 1}, timeout=timeout)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    return {c["data"]["name"]: c["data"].get("selftext") if c["kind"] == "t3" else c["data"].get("body")
            for c in resp.json()["data"]["children"]}


def do_edit(fullname, text):
    """Returns None on success, else (message, status)."""
    text = (text or '').strip()
    if not _FULLNAME_RE.match(fullname or '') or not text or len(text) > _TEXT_MAX[fullname[:2]]:
        return 'Invalid text', 400
    try:
        reddit_actions.edit(fullname, text)
    except reddit_actions.ActionError as e:
        return str(e), e.status
    return None


def do_delete(fullname):
    if not _FULLNAME_RE.match(fullname or ''):
        return 'Invalid item', 400
    try:
        reddit_actions.delete(fullname)
    except reddit_actions.ActionError as e:
        return str(e), e.status
    return None


def do_avatar(upload):
    """`upload` is a werkzeug FileStorage, or None to remove the picture."""
    try:
        if upload is None:
            reddit_actions.remove_avatar()
            return None
        blob = upload.read(_AVATAR_MAX + 1)
        if upload.mimetype not in _AVATAR_TYPES or not blob:
            return 'Use a JPEG or PNG image.', 415
        if len(blob) > _AVATAR_MAX:
            return 'That image is too large.', 413
        reddit_actions.set_avatar(upload.filename or 'icon', upload.mimetype, blob)
    except reddit_actions.ActionError as e:
        return str(e), e.status
    return None


def _reply(err, **ok):
    return (jsonify({"error": err[0]}), err[1]) if err else (jsonify({"ok": True, **ok}), 200)


@bp.route("/api/account")
def get_account():
    require_account(post=False)
    try:
        return jsonify(fetch_user_about(reddit_login.username())), 200, _NO_STORE
    except UpstreamError as e:
        return e.response()


@bp.route("/api/account/<kind>")
def get_account_items(kind):
    """The account's own posts or comments, uncached so edits and deletes show up straight away."""
    require_account(post=False)
    if kind not in ('posts', 'comments'):
        return jsonify({"error": "Not found"}), 404
    fetch = fetch_user_posts if kind == 'posts' else fetch_user_comments
    try:
        return jsonify(fetch(reddit_login.username(), 'new', '', request.args.get('after', ''))), 200, _NO_STORE
    except UpstreamError as e:
        return e.response()


@bp.route("/api/account/text")
def get_account_text():
    require_account(post=False)
    fullname = request.args.get('id', '')
    if not _FULLNAME_RE.match(fullname):
        return jsonify({"error": "Invalid item"}), 400
    try:
        return jsonify({"text": fetch_full_texts([fullname]).get(fullname) or ""}), 200, _NO_STORE
    except UpstreamError as e:
        return e.response()


@bp.route("/api/account/edit", methods=["POST"])
def api_edit():
    require_account()
    b = request.get_json(silent=True) or {}
    return _reply(do_edit(str(b.get('id', '')), str(b.get('text', ''))))


@bp.route("/api/account/delete", methods=["POST"])
def api_delete():
    require_account()
    return _reply(do_delete(str((request.get_json(silent=True) or {}).get('id', ''))))


@bp.route("/api/account/avatar", methods=["POST"])
def api_avatar():
    require_account()
    f = request.files.get('file')
    return _reply(do_avatar(f) if f and f.filename else ('No file', 400))


@bp.route("/api/account/avatar/remove", methods=["POST"])
def api_avatar_remove():
    require_account()
    return _reply(do_avatar(None))


def _form_done(err):
    return err if err else redirect(safe_next(request.form.get('next') or '/account'))


@bp.route("/actions/account/edit", methods=["POST"])
def form_edit():
    require_account()
    return _form_done(do_edit(request.form.get('id', ''), request.form.get('text', '')))


@bp.route("/actions/account/delete", methods=["POST"])
def form_delete():
    require_account()
    return _form_done(do_delete(request.form.get('id', '')))


@bp.route("/actions/account/avatar", methods=["POST"])
def form_avatar():
    require_account()
    f = request.files.get('file')
    if request.form.get('remove'):
        return _form_done(do_avatar(None))
    return _form_done(do_avatar(f) if f and f.filename else ('Choose an image first.', 400))
