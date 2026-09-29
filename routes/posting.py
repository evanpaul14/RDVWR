"""Creating posts as the logged-in Reddit account: uploads, flair options and the submit
itself. /api/* is for the JS app; /submit is the no-JS form. Same local-only rules as
routes/actions.py."""
import re
from urllib.parse import urlsplit
from flask import Blueprint, jsonify, redirect, render_template, request
from helpers import DISABLE_DOWNLOADS, SUBREDDIT_RE, UpstreamError, validate_params
from reddit_client import reddit_get
import reddit_actions
import reddit_media
from routes.actions import require_account

bp = Blueprint("posting", __name__)

_TITLE_MAX, _BODY_MAX, _URL_MAX, _GALLERY_MAX = 300, 40000, 2000, 20
_FLAIR_ID_RE = re.compile(r'^[0-9a-fA-F-]{8,40}$')
_ASSET_RE = re.compile(r'^[a-z0-9]{6,20}$')


def fetch_flairs(subreddit, timeout=10):
    """A subreddit's post flairs a regular member may pick, and whether one is required."""
    resp = reddit_get(f"https://www.reddit.com/r/{subreddit}/api/link_flair_v2.json",
                      params={"raw_json": 1}, timeout=timeout)
    if resp.status_code in (403, 404):
        return {"flairs": [], "required": False}   # flair disabled or not for us to choose
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    flairs = [{"id": f["id"], "text": f.get("text") or "", "editable": bool(f.get("text_editable")),
               "text_color": f.get("text_color") or "dark", "background": f.get("background_color") or ""}
              for f in resp.json() if f.get("id") and not f.get("mod_only")]
    required = False
    try:
        reqs = reddit_get(f"https://www.reddit.com/api/v1/{subreddit}/post_requirements", timeout=timeout)
        required = bool(reqs.ok and reqs.json().get("is_flair_required"))
    except Exception:
        pass
    return {"flairs": flairs, "required": required}


@bp.route("/api/r/<subreddit>/flairs")
@validate_params(subreddit=SUBREDDIT_RE)
def get_flairs(subreddit):
    require_account(post=False)
    try:
        return jsonify(fetch_flairs(subreddit)), 200, {'Cache-Control': 'private, no-store'}
    except UpstreamError as e:
        return e.response()


@bp.route("/api/upload", methods=["POST"])
def api_upload():
    """Upload one image/GIF/video to Reddit; the result is then attached to a post or comment."""
    require_account()
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({"error": "No file"}), 400
    try:
        return jsonify(reddit_media.upload(f.filename, f.mimetype, f.read()))
    except reddit_actions.ActionError as e:
        return jsonify({"error": str(e)}), e.status


def _clean_media(items, kind):
    """Only accept items that came back from /api/upload (Reddit's own upload hosts)."""
    media = []
    for m in items if isinstance(items, list) else []:
        if not isinstance(m, dict) or not _ASSET_RE.match(str(m.get('asset_id', ''))) or not reddit_media.is_upload_url(m.get('url')):
            return None
        media.append({'asset_id': m['asset_id'], 'url': m['url'], 'poster_url': m.get('poster_url')})
    if kind == 'gallery':
        return media if 2 <= len(media) <= _GALLERY_MAX else None
    if kind == 'image':
        return media if len(media) == 1 else None
    return media if len(media) == 1 and reddit_media.is_upload_url(media[0]['poster_url']) else None   # video


def do_submit(sub, title, kind, body='', media=None, flair_id='', flair_text=''):
    """Validate and create a post; returns (path of the new post, None) or (None, (message, status))."""
    sub, title, body = (sub or '').strip().removeprefix('r/'), (title or '').strip(), (body or '').strip()
    flair_id, flair_text = (flair_id or '').strip(), (flair_text or '').strip()[:64]
    if not SUBREDDIT_RE.match(sub) or '+' in sub:
        return None, ('Pick a single subreddit to post in.', 400)
    if not title or len(title) > _TITLE_MAX:
        return None, (f'A title of 1–{_TITLE_MAX} characters is required.', 400)
    if flair_id and not _FLAIR_ID_RE.match(flair_id):
        return None, ('Invalid flair', 400)
    if kind == 'link':
        if not re.match(r'^https?://\S+$', body) or len(body) > _URL_MAX:
            return None, ('A link post needs a full http(s) URL.', 400)
    elif kind == 'self':
        if len(body) > _BODY_MAX:
            return None, ('That text is too long.', 400)
    elif kind in ('image', 'video', 'gallery'):
        media = _clean_media(media, kind)
        if not media:
            return None, ({'gallery': f'A gallery needs 2–{_GALLERY_MAX} images.', 'image': 'Attach one image.',
                           'video': 'Attach one video.'}[kind], 400)
    else:
        return None, ('Invalid post', 400)
    try:
        url = reddit_actions.submit(sub, title, kind, text=body, url=body, media=media or (),
                                    flair_id=flair_id, flair_text=flair_text)
    except reddit_actions.ActionError as e:
        return None, (str(e), e.status)
    path = urlsplit(url).path
    return (path if path.startswith('/r/') else '/'), None


@bp.route("/api/submit", methods=["POST"])
def api_submit():
    require_account()
    b = request.get_json(silent=True) or {}
    path, err = do_submit(str(b.get('sub', '')), str(b.get('title', '')), str(b.get('kind', '')), str(b.get('body', '')),
                          b.get('media'), str(b.get('flair_id') or ''), str(b.get('flair_text') or ''))
    return (jsonify({"error": err[0]}), err[1]) if err else (jsonify({"path": path}), 200)


def _render_submit(values, error=None, status=200):
    sub = (values.get('sub') or '').strip().removeprefix('r/')
    flairs = {"flairs": [], "required": False}
    if SUBREDDIT_RE.match(sub) and '+' not in sub:
        try:
            flairs = fetch_flairs(sub)
        except Exception:
            pass
    ctx = {"ns_view": "submit", "page_title": "New post — RDVWR",
           "page": {"form": values, "error": error, "flairs": flairs["flairs"], "flair_required": flairs["required"]}}
    return render_template("index.html", disable_downloads=DISABLE_DOWNLOADS, **ctx), status, {'Cache-Control': 'no-store'}


def _upload_form_files():
    """Upload the no-JS form's attached files; returns (kind, media) or raises ActionError."""
    files = [f for f in request.files.getlist('files') if f and f.filename]
    if not files:
        return None, []
    media = [reddit_media.upload(f.filename, f.mimetype, f.read()) for f in files]
    if any(m['type'] == 'video' for m in media):
        if len(media) > 1:
            raise reddit_actions.ActionError('A video post takes a single video.', 400)
        return 'video', media
    return ('image' if len(media) == 1 else 'gallery'), media


@bp.route("/submit", methods=["GET", "POST"])
def submit_page():
    """The no-JS post form (the JS app has its own modal)."""
    if request.method == "GET":
        require_account(post=False)
        values = {k: request.args.get(k, '') for k in ('sub', 'title', 'body', 'kind')}
        return _render_submit({**values, "kind": values['kind'] if values['kind'] in ('self', 'link') else 'self'})
    require_account()
    values = {k: request.form.get(k, '') for k in ('sub', 'title', 'kind', 'body', 'flair_id', 'flair_text')}
    try:
        media_kind, media = _upload_form_files()
    except reddit_actions.ActionError as e:
        return _render_submit(values, str(e), e.status)
    path, err = do_submit(values['sub'], values['title'], media_kind or values['kind'], values['body'], media,
                          values['flair_id'], values['flair_text'])
    return _render_submit(values, err[0], err[1]) if err else redirect(path)
