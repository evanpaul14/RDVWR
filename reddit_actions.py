"""Things done as the logged-in Reddit account (see reddit_login.py): the write side.
Every call needs a request the login may be used for (a local one, see
reddit_login.request_token) and raises ActionError otherwise."""
import json
import time
from curl_cffi import CurlMime, requests as cffi_requests
import reddit_login
from reddit_client import PROXIES, reddit_get

API = 'https://oauth.reddit.com'


class ActionError(Exception):
    """A failed action; `status` is the HTTP status to answer our own caller with."""
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _post(path, data=None, *, json_body=None, timeout=15, raw=False):
    """POST to Reddit's API as the account: form `data`, or `json_body` for the few JSON endpoints.
    A `_file` entry in `data` (filename, mimetype, bytes) makes it a multipart upload; `raw` sends
    `data` as is, for the older endpoints that don't take api_type."""
    token = reddit_login.request_token()
    if not token:
        raise ActionError('Not logged in to Reddit.', 401)
    if json_body is not None:
        body = {'json': {**json_body, 'api_type': 'json', 'raw_json': 1}}
    else:
        data = dict(data) if raw else {**data, 'api_type': 'json', 'raw_json': 1}
        upload = data.pop('_file', None)
        body = {'data': data}
        if upload:
            mime = CurlMime()
            mime.addpart(name='file', filename=upload[0], content_type=upload[1], data=upload[2])
            body['multipart'] = mime
    try:
        r = cffi_requests.post(API + path, headers={'Authorization': f'Bearer {token}'},
                               impersonate='chrome131', proxies=PROXIES, timeout=timeout, **body)
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


def edit(fullname, text):
    """Replace the text of one of the account's own text posts (t3_) or comments (t1_)."""
    _post('/api/editusertext', {'thing_id': fullname, 'text': text})


def delete(fullname):
    """Delete one of the account's own posts or comments."""
    _post('/api/del', {'id': fullname})


def _profile_icon(path, **fields):
    """Send a profile-picture change to the account's own profile (its user subreddit, u_<name>)."""
    body = _post(f'/r/u_{reddit_login.username()}/api/{path}', fields, raw=True)
    errs = body.get('errors') if isinstance(body, dict) else None
    if errs:
        raise ActionError(f"Reddit refused the picture: {errs[0] if isinstance(errs[0], str) else ' '.join(map(str, errs[0]))}", 400)


def set_avatar(filename, mimetype, blob):
    """Upload a new profile picture (JPEG or PNG)."""
    _profile_icon('upload_sr_img', upload_type='icon', name='icon', img_type='png' if mimetype == 'image/png' else 'jpg',
                  _file=(filename, mimetype, blob))


def remove_avatar():
    _profile_icon('delete_sr_icon')


def comment(parent, text, media_id=None):
    """Reply to a post (t3_) or comment (t1_), optionally with one uploaded image/GIF
    (`media_id`, see reddit_media). Returns Reddit's raw data for the new comment.

    Reddit keeps a comment's image in its rich-text form, so a comment with media is sent
    as rich text: `text` becomes plain paragraphs (markdown formatting isn't applied)."""
    if media_id:
        doc = [{'e': 'par', 'c': [{'e': 'text', 't': line}]} for line in (text or '').split('\n') if line.strip()]
        doc.append({'e': 'img', 'id': media_id, 'c': ''})
        body = _post('/api/comment', {'thing_id': parent, 'richtext_json': json.dumps({'document': doc})})
    else:
        body = _post('/api/comment', {'thing_id': parent, 'text': text})
    try:
        made = body['json']['data']['things'][0]['data']
    except (KeyError, IndexError, TypeError):
        if not (isinstance(body, dict) and body.get('name', '').startswith('t1_')):
            raise ActionError("Reddit didn't return the new comment.")
        made = body   # Reddit sometimes answers with the bare comment
    return _finished_comment(made) if media_id else made


def _finished_comment(made, tries=8):
    """Reddit answers an image comment with a "Processing img…" placeholder while it
    prepares the picture; re-read the comment until the real body is there."""
    for _ in range(tries):
        if 'Processing img' not in (made.get('body') or ''):
            return made
        time.sleep(1.5)
        try:
            r = reddit_get('https://www.reddit.com/api/info.json', params={'id': made['name'], 'raw_json': 1}, timeout=10)
            children = r.json()['data']['children'] if r.ok else []
        except Exception:
            continue
        if children:
            made = {**made, **children[0]['data'], 'likes': True}
    return made


def _await_new_post(title, since, timeout=20):
    """Image and video posts are created asynchronously (Reddit answers with a websocket
    URL); poll the account's own submissions for the one just made."""
    name = reddit_login.username()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        try:
            r = reddit_get(f'https://www.reddit.com/user/{name}/submitted.json',
                           params={'limit': 5, 'raw_json': 1}, timeout=10)
            children = r.json()['data']['children'] if r.ok else []
        except Exception:
            continue
        for c in children:
            if c['data']['title'] == title and c['data']['created_utc'] >= since - 120:
                return 'https://www.reddit.com' + c['data']['permalink']
    raise ActionError('Reddit is still processing that upload; check your profile in a minute.', 504)


def submit(subreddit, title, kind='self', text='', url='', media=(), flair_id='', flair_text=''):
    """Create a post in a single subreddit and return its Reddit URL.

    kind: self (text), link (`url`), image / video (one item of `media`), gallery (2+ images).
    `media` items are reddit_media.upload() results. Flair is optional."""
    flair = {'flair_id': flair_id, **({'flair_text': flair_text} if flair_text else {})} if flair_id else {}
    if kind == 'gallery':
        body = _post('/api/submit_gallery_post.json', json_body={
            'sr': subreddit, 'title': title, 'kind': 'self', 'sendreplies': True, 'show_error_list': True,
            'validate_on_submit': True, **flair,
            'items': [{'caption': '', 'outbound_url': '', 'media_id': m['asset_id']} for m in media]}, timeout=30)
    else:
        data = {'sr': subreddit, 'title': title, 'kind': kind, 'resubmit': 'true', 'sendreplies': 'true', **flair}
        if kind == 'self':
            data['text'] = text
        elif kind == 'link':
            data['url'] = url
        else:
            data['url'] = media[0]['url']
            if kind == 'video':
                data['video_poster_url'] = media[0]['poster_url']
        started = time.time()
        body = _post('/api/submit', data, timeout=30)
        if kind in ('image', 'video'):
            return _await_new_post(title, started)
    try:
        return body['json']['data']['url']
    except (KeyError, TypeError):
        raise ActionError("Reddit didn't confirm the post; check your profile before retrying.")
