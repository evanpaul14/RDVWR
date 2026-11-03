"""Owner passphrase for REDDIT_LOGIN: lets the instance owner use the logged-in Reddit account
from any device, over the reverse proxy, without an SSH tunnel.

Set REDDIT_LOGIN_OWNER_KEY to a long random string. Visiting /auth/owner and entering it sets
a signed, HttpOnly cookie; reddit_login.is_local_request() then treats that browser like a
request from the server's own machine. Unset, none of this exists. Changing the key
invalidates every owner cookie. Serve over HTTPS so the key and cookie aren't sent in clear."""
import hashlib
import hmac
import os
import time

from flask import request

KEY = os.environ.get('REDDIT_LOGIN_OWNER_KEY', '').strip()
COOKIE = 'rdvwr_owner'
MAX_AGE = 90 * 86400
_FAIL_LIMIT, _FAIL_WINDOW = 5, 600
_fails = {}


def _sig(ts):
    return hmac.new(KEY.encode(), f'owner-v1:{ts}'.encode(), hashlib.sha256).hexdigest()


def make_token():
    ts = str(int(time.time()))
    return f'{ts}.{_sig(ts)}'


def is_owner():
    if not KEY:
        return False
    ts, _, sig = request.cookies.get(COOKIE, '').partition('.')
    if not ts.isdigit() or not sig or time.time() - int(ts) > MAX_AGE:
        return False
    return hmac.compare_digest(sig, _sig(ts))


def _client():
    return request.headers.get('X-Real-IP') or request.remote_addr or ''


def check_key(candidate):
    """Constant-time key check with a per-client lockout after repeated failures."""
    now = time.time()
    who = _client()
    recent = [t for t in _fails.get(who, []) if now - t < _FAIL_WINDOW]
    if len(recent) >= _FAIL_LIMIT:
        _fails[who] = recent
        return False
    if KEY and hmac.compare_digest(candidate.encode(), KEY.encode()):
        _fails.pop(who, None)
        return True
    _fails[who] = recent + [now]
    return False
