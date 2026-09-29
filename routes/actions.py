"""Actions as the logged-in Reddit account (voting, joining subreddits). Like login, these only answer
requests from the server's own machine while logged in (404 otherwise), and need a
same-site request. /api/* is for the JS app; /actions/* are the no-JS form equivalents."""
import re
from flask import Blueprint, abort, jsonify, redirect, request
from helpers import SUBREDDIT_RE, is_same_site_request, safe_next
import reddit_actions
import reddit_login

bp = Blueprint("actions", __name__)

_FULLNAME_RE = re.compile(r'^t[13]_[a-z0-9]{1,12}$')


def _require_account():
    if not reddit_login.request_account_active() or not reddit_login.username():
        abort(404)
    if not is_same_site_request():
        abort(403)


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
