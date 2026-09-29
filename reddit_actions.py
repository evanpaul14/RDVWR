"""Things done as the logged-in Reddit account (see reddit_login.py): the write side.
Every call needs a request the login may be used for (a local one, see
reddit_login.request_token) and raises ActionError otherwise."""
from curl_cffi import requests as cffi_requests
import reddit_login
from reddit_client import PROXIES

API = 'https://oauth.reddit.com'


class ActionError(Exception):
    """A failed action; `status` is the HTTP status to answer our own caller with."""
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _post(path, data, timeout=15):
    token = reddit_login.request_token()
    if not token:
        raise ActionError('Not logged in to Reddit.', 401)
    try:
        r = cffi_requests.post(API + path, data={**data, 'api_type': 'json', 'raw_json': 1},
                               headers={'Authorization': f'Bearer {token}'},
                               impersonate='chrome131', proxies=PROXIES, timeout=timeout)
    except Exception as e:
        raise ActionError(f"Couldn't reach Reddit: {e}")
    if r.status_code in (401, 403):
        raise ActionError('Reddit rejected the login; log in again.', 401)
    if r.status_code == 429:
        raise ActionError('Reddit is rate limiting you; try again shortly.', 429)
    if not r.ok:
        raise ActionError(f'Reddit returned HTTP {r.status_code}.')
    try:
        body = r.json()
    except ValueError:
        return {}
    errors = (body.get('json') or {}).get('errors') if isinstance(body, dict) else None
    if errors:
        raise ActionError(' '.join(str(e[1]) for e in errors if len(e) > 1) or 'Reddit refused that.', 400)
    return body


def vote(fullname, direction):
    """direction: 1 upvote, -1 downvote, 0 clear. fullname is t3_<post> or t1_<comment>."""
    _post('/api/vote', {'id': fullname, 'dir': direction})


def subscribe(subreddit, join):
    """Join (join=True) or leave a single subreddit."""
    _post('/api/subscribe', {'action': 'sub' if join else 'unsub', 'sr_name': subreddit})


def comment(parent, text):
    """Reply to a post (t3_) or comment (t1_). Returns Reddit's raw data for the new comment."""
    body = _post('/api/comment', {'thing_id': parent, 'text': text})
    try:
        return body['json']['data']['things'][0]['data']
    except (KeyError, IndexError, TypeError):
        raise ActionError("Reddit didn't return the new comment.")


def submit(subreddit, title, text=None, url=None):
    """Create a text post (`text`, may be empty) or a link post (`url`) in a single subreddit.
    Returns the new post's Reddit URL. Flair, images and video aren't supported."""
    data = {'sr': subreddit, 'title': title, 'kind': 'link' if url else 'self', 'resubmit': 'true'}
    data.update({'url': url} if url else {'text': text or ''})
    body = _post('/api/submit', data, timeout=20)
    try:
        return body['json']['data']['url']
    except (KeyError, TypeError):
        raise ActionError("Reddit didn't confirm the post; check your profile before retrying.")
