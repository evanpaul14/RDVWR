"""Optional Reddit account login (REDDIT_LOGIN=1), kept local to this server.

Reddit no longer hands out API apps, so there's no OAuth flow: you sign in on reddit.com
yourself (CAPTCHA, 2FA and all), then paste that browser session's cookies into the
settings form. We check them against Reddit and keep them in a 0600 file next to the app
(REDDIT_LOGIN_FILE). They're never sent to the browser or anywhere but Reddit.

Only login/logout/status for now; cookie_header() is the hook for anything that later
needs to act as the account."""
import base64
import ipaddress
import json
import logging
import os
import re
import threading
import time

from curl_cffi import requests as cffi_requests
from flask import has_request_context, request
import reddit_client
import reddit_owner
from reddit_client import PROXIES

log = logging.getLogger(__name__)

ENABLED = os.environ.get('REDDIT_LOGIN', '0').strip().lower() in ('1', 'true', 'yes', 'on')
STORE_PATH = os.environ.get('REDDIT_LOGIN_FILE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '.reddit_login.json')

_lock = threading.Lock()
_token_lock = threading.Lock()
_token = {'cookies': None, 'value': None, 'exp': 0.0}
_TOKEN_MARGIN = 300  # re-mint this many seconds before token_v2 expires
_COOKIE_RE = re.compile(r'^[\w.\-]+=[^;\s]*(;\s*[\w.\-]+=[^;\s]*)*;?$')


def _load():
    try:
        with open(STORE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data):
    tmp = STORE_PATH + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, STORE_PATH)


def _clean(raw):
    raw = re.sub(r'^\s*cookie:\s*', '', raw or '', flags=re.I).strip()
    if not _COOKIE_RE.match(raw):
        raise ValueError('That does not look like a cookie header (name=value; name=value).')
    return raw


def _whoami(cookies):
    """Ask Reddit who these cookies belong to; None if they're not a live login."""
    r = cffi_requests.get('https://www.reddit.com/api/me.json', impersonate='chrome131', proxies=PROXIES, timeout=15,
                          headers={'Cookie': cookies})
    if not r.ok:
        raise ValueError(f'Reddit answered HTTP {r.status_code}.')
    try:
        return (r.json().get('data') or {}).get('name')
    except ValueError:
        raise ValueError('Reddit did not return account info (blocked or not signed in).')


def username():
    """Logged-in account name, or None. Reads only the local file, no network."""
    return _load().get('username') if ENABLED else None


def login_with_cookies(raw):
    cookies = _clean(raw)
    name = _whoami(cookies)
    if not name:
        raise ValueError('Those cookies are not signed in to a Reddit account.')
    with _lock:
        _save({'username': name, 'cookies': cookies})
    log.info('reddit login: signed in as u/%s', name)
    return name


def cookie_header():
    """The stored Cookie header for the logged-in account, or None."""
    return _load().get('cookies') if ENABLED else None


_FORWARD_HEADERS = ('X-Forwarded-For', 'X-Real-IP', 'Forwarded')


def is_local_request():
    """True for a request straight from this machine (not via a reverse proxy), or one from a
    browser that holds the owner cookie (reddit_owner.py)."""
    if not has_request_context():
        return False
    if reddit_owner.is_owner():
        return True
    if any(h in request.headers for h in _FORWARD_HEADERS):
        return False
    try:
        return ipaddress.ip_address(request.remote_addr or '').is_loopback
    except ValueError:
        return False


def request_cookie_header():
    """The login's cookies, but only for a local request: the account belongs to whoever
    owns the instance, so public visitors never get to act (or browse) as it."""
    return cookie_header() if is_local_request() else None


def login_status():
    """Context for the settings UIs; None when login is switched off."""
    if not ENABLED:
        return None
    local = is_local_request()
    return {'available': local, 'username': username() if local else None}


def _jwt_exp(tok):
    try:
        payload = tok.split('.')[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))['exp'])
    except Exception:
        return time.time() + 3600


def access_token():
    """A bearer token for oauth.reddit.com acting as the logged-in account, or None.

    Reddit's web client authenticates its API calls with `token_v2` (about a day), which
    the home page hands out to anyone holding a live `reddit_session` cookie. Same
    thing here: mint one from the stored cookies, cache it in memory, re-mint near expiry."""
    cookies = cookie_header()
    if not cookies:
        return None
    with _token_lock:
        if _token['cookies'] == cookies and _token['value'] and time.time() < _token['exp'] - _TOKEN_MARGIN:
            return _token['value']
        # A still-valid token_v2 in the header makes Reddit skip issuing a new one, so mint without it.
        mint_cookies = re.sub(r'(^|;\s*)token_v2=[^;]*;?\s*', r'\1', cookies).strip().rstrip(';')
        try:
            r = cffi_requests.get('https://www.reddit.com/', impersonate='chrome131', proxies=PROXIES, timeout=15,
                                  allow_redirects=False, headers={'Cookie': mint_cookies})
        except Exception as e:
            log.warning('reddit login: token mint failed: %s', e)
            return None
        for sc in r.headers.get_list('set-cookie'):
            if sc.startswith('token_v2='):
                tok = sc.split(';', 1)[0][len('token_v2='):]
                _token.update(cookies=cookies, value=tok, exp=_jwt_exp(tok))
                return tok
        log.warning('reddit login: no token_v2 in response (HTTP %s); cookies expired?', r.status_code)
        return None


def request_account_active():
    """True when this request should act as the login: a local request while logged in."""
    return bool(ENABLED and has_request_context() and is_local_request() and cookie_header())


def request_token():
    """access_token(), but only for a request that request_account_active() says may use it."""
    return access_token() if request_account_active() else None


def logout():
    _token.update(cookies=None, value=None, exp=0.0)
    with _lock:
        try:
            os.remove(STORE_PATH)
        except OSError:
            pass


reddit_client.USER_TOKEN_PROVIDER = request_token
