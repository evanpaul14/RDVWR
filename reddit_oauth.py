"""OAuth refresh-token login as an alternative to pasted cookies (see reddit_login.py).

Reddit no longer registers API apps, so this borrows the public client id the Android app
uses (reddit_client._REDDIT_ANDROID_CLIENT_ID) for the standard "installed app" flow: you
authorize on reddit.com yourself (password, 2FA and all), the browser is sent to
reddit://redirect?code=..., which it can't open, and you paste that address back. We trade the
code for a permanent refresh token, kept in the same 0600 file as the cookie login, and
mint short-lived access tokens from it. Reddit's terms don't sanction using another app's
client id, so this is opt-in and only ever for your own account."""
import base64
import logging
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

from curl_cffi import requests as cffi_requests
from reddit_client import PROXIES, _REDDIT_ANDROID_CLIENT_ID

log = logging.getLogger(__name__)

REDIRECT_URI = 'reddit://redirect'
SCOPES = 'identity read submit edit vote subscribe history mysubreddits flair account save report privatemessages'
_TOKEN_URL = 'https://www.reddit.com/api/v1/access_token'
_UA = 'Reddit/Version 2024.16.0/Build 1551366/Android 11'
_pending = {}   # state -> issued-at, so a pasted address must answer a link we handed out


def authorize_url():
    now = time.time()
    for k in [k for k, t in _pending.items() if now - t > 900]:
        del _pending[k]
    state = secrets.token_urlsafe(16)
    _pending[state] = now
    return 'https://www.reddit.com/api/v1/authorize?' + urlencode({
        'client_id': _REDDIT_ANDROID_CLIENT_ID, 'response_type': 'code', 'state': state,
        'redirect_uri': REDIRECT_URI, 'duration': 'permanent', 'scope': SCOPES})


def _token_request(data):
    auth = base64.b64encode(f'{_REDDIT_ANDROID_CLIENT_ID}:'.encode()).decode()
    r = cffi_requests.post(_TOKEN_URL, data=data, headers={'Authorization': f'Basic {auth}', 'User-Agent': _UA},
                           impersonate='chrome131', proxies=PROXIES, timeout=15)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if not r.ok or 'access_token' not in body:
        raise ValueError(f"Reddit refused the token request ({body.get('error') or f'HTTP {r.status_code}'}).")
    return body


def exchange(pasted):
    """Trade a pasted reddit://redirect?code=…&state=… address (or a bare code) for tokens.
    Returns (refresh_token, access_token, expires_in)."""
    pasted = (pasted or '').strip()
    if '?' in pasted or pasted.startswith('reddit:'):
        q = parse_qs(urlparse(pasted).query)
        if 'error' in q:
            raise ValueError(f"Reddit said: {q['error'][0]}.")
        code, state = (q.get('code') or [''])[0], (q.get('state') or [''])[0]
        if state not in _pending:
            raise ValueError('That address is not from a link made by this server (or it timed out). Start again.')
        del _pending[state]
    else:
        code = pasted.split('#')[0]
    if not code:
        raise ValueError('No code found in what you pasted.')
    body = _token_request({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT_URI})
    if not body.get('refresh_token'):
        raise ValueError('Reddit did not return a refresh token.')
    return body['refresh_token'], body['access_token'], body.get('expires_in', 3600)


def refresh(refresh_token):
    """(access_token, expires_in) from a stored refresh token."""
    body = _token_request({'grant_type': 'refresh_token', 'refresh_token': refresh_token})
    return body['access_token'], body.get('expires_in', 3600)


def whoami(access_token):
    r = cffi_requests.get('https://oauth.reddit.com/api/v1/me', impersonate='chrome131', proxies=PROXIES, timeout=15,
                          headers={'Authorization': f'Bearer {access_token}', 'User-Agent': _UA})
    return (r.json().get('name') if r.ok else None)
