"""Actions as the logged-in Reddit account (voting, joining subreddits, commenting; posting is in posting.py). Like login, these only answer
requests from the server's own machine while logged in (404 otherwise), and need a
same-site request. /api/* is for the JS app; /actions/* are the no-JS form equivalents."""
import re
from flask import Blueprint, abort, jsonify, redirect, request
from helpers import SUBREDDIT_RE, is_same_site_request, safe_next
import reddit_actions
import reddit_login
import reddit_media
from routes.comments import _parse_comment_fields

bp = Blueprint("actions", __name__)

_FULLNAME_RE = re.compile(r'^t[13]_[a-z0-9]{1,12}$')


def require_account(post=True):
    """404 unless this is a local request while logged in; POSTs must also be same-site."""
    if not reddit_login.request_account_active() or not reddit_login.username():
        abort(404)
    if post and not is_same_site_request():
        abort(403)


_require_account = require_account


def _do_vote(fullname, direction):
    """Validate and cast a vote; returns an error message, or None on success."""
    if not _FULLNAME_RE.match(fullname or '') or direction not in ('1', '0', '-1'):
        return 'Invalid vote', 400
    try:
        reddit_actions.vote(fullname, int(direction))
    except reddit_actions.ActionError as e:
        return str(e), e.status
    return None


@bp.route("/api/vote", methods=["POST"])
def api_vote():
    _require_account()
    body = request.get_json(silent=True) or {}
    err = _do_vote(str(body.get('id', '')), str(body.get('dir', '')))
    return (jsonify({"error": err[0]}), err[1]) if err else (jsonify({"ok": True}), 200)


@bp.route("/actions/vote", methods=["POST"])
def form_vote():
    _require_account()
    err = _do_vote(request.form.get('id', ''), request.form.get('dir', ''))
    return err if err else redirect(safe_next(request.form.get('next')))


def _do_subscribe(subreddit, action):
    """Join/leave a subreddit; returns an error (message, status), or None on success."""
    if not SUBREDDIT_RE.match(subreddit or '') or '+' in subreddit or action not in ('sub', 'unsub'):
        return 'Invalid subreddit', 400
    try:
        reddit_actions.subscribe(subreddit, action == 'sub')
    except reddit_actions.ActionError as e:
        return str(e), e.status
    return None


@bp.route("/api/subscribe", methods=["POST"])
def api_subscribe():
    _require_account()
    body = request.get_json(silent=True) or {}
    err = _do_subscribe(str(body.get('sub', '')), str(body.get('action', '')))
    return (jsonify({"error": err[0]}), err[1]) if err else (jsonify({"ok": True}), 200)


@bp.route("/actions/subscribe", methods=["POST"])
def form_subscribe():
    _require_account()
    err = _do_subscribe(request.form.get('sub', ''), request.form.get('action', ''))
    return err if err else redirect(safe_next(request.form.get('next')))


_COMMENT_MAX = 10000


_MEDIA_ID_RE = re.compile(r'^[a-z0-9]{6,20}$')


def do_comment(parent, text, media_id=None):
    """Post a comment (optionally with an uploaded image); returns (data, None) or (None, (message, status))."""
    text = (text or '').strip()
    if (not _FULLNAME_RE.match(parent or '') or len(text) > _COMMENT_MAX or (media_id and not _MEDIA_ID_RE.match(media_id))
            or not (text or media_id)):
        return None, ('Invalid comment', 400)
    try:
        return reddit_actions.comment(parent, text, media_id or None), None
    except reddit_actions.ActionError as e:
        return None, (str(e), e.status)


@bp.route("/api/comment", methods=["POST"])
def api_comment():
    _require_account()
    body = request.get_json(silent=True) or {}
    data, err = do_comment(str(body.get('parent', '')), str(body.get('text', '')), str(body.get('media_id') or ''))
    if err:
        return jsonify({"error": err[0]}), err[1]
    return jsonify({"comment": {**_parse_comment_fields(data), "likes": data.get("likes", True)}}), 200


@bp.route("/actions/comment", methods=["POST"])
def form_comment():
    _require_account()
    media_id = None
    upload = request.files.get('image')
    if upload and upload.filename:
        try:
            media_id = reddit_media.upload(upload.filename, upload.mimetype, upload.read())['asset_id']
        except reddit_actions.ActionError as e:
            return str(e), e.status
    _, err = do_comment(request.form.get('parent', ''), request.form.get('text', ''), media_id)
    return err if err else redirect(safe_next(request.form.get('next')))
