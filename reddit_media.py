"""Uploading images, GIFs and video to Reddit as the logged-in account, for posts and
comments (see reddit_actions.py). Reddit's flow: ask for an upload lease, send the file to
the S3 bucket it names, then refer to the result by asset id / URL when posting."""
import mimetypes
import os
import shutil
import subprocess
import tempfile
import requests
from reddit_actions import ActionError, _post
from reddit_client import PROXIES

IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
VIDEO_TYPES = {'video/mp4', 'video/quicktime'}
MAX_IMAGE = 20 * 1024 * 1024
MAX_VIDEO = 100 * 1024 * 1024   # Reddit allows more; the file is held in memory here
UPLOAD_HOSTS = ('https://reddit-uploaded-media.s3-accelerate.amazonaws.com/',
                'https://reddit-uploaded-video.s3-accelerate.amazonaws.com/')


def is_upload_url(url):
    return isinstance(url, str) and url.startswith(UPLOAD_HOSTS)


def _send(filename, mimetype, blob):
    """Lease an upload slot and PUT the file into it. Returns (asset_id, url)."""
    lease = _post('/api/media/asset.json', {'filepath': filename, 'mimetype': mimetype})
    try:
        args = lease['args']
        fields = {f['name']: f['value'] for f in args['fields']}
        asset_id, action = lease['asset']['asset_id'], 'https:' + args['action']
    except (KeyError, TypeError):
        raise ActionError("Reddit didn't accept the upload; try again in a moment.")
    try:
        r = requests.post(action, data=fields, files={'file': (filename, blob, mimetype)},
                          proxies=PROXIES, timeout=180)
    except requests.RequestException as e:
        raise ActionError(f"Couldn't upload the file: {e}")
    if r.status_code not in (200, 201, 204):
        raise ActionError(f'Reddit storage refused the file (HTTP {r.status_code}).')
    return asset_id, f"{action}/{fields['key']}"


def video_poster(blob):
    """A JPEG frame from the video: Reddit requires a poster image for video posts."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ActionError('Video posts need ffmpeg installed on the server.')
    with tempfile.NamedTemporaryFile(suffix='.mp4') as tmp:
        tmp.write(blob)
        tmp.flush()
        for seek in ('1', '0'):   # a moment in, else the very first frame (very short clips)
            out = subprocess.run([ffmpeg, '-loglevel', 'error', '-ss', seek, '-i', tmp.name, '-frames:v', '1',
                                  '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'],
                                 capture_output=True, timeout=60).stdout
            if out:
                return out
    raise ActionError("Couldn't read a frame from that video.")


def upload(filename, mimetype, blob):
    """Upload one file. Returns {"asset_id", "url", "type": "image"|"video"[, "poster_url"]}."""
    filename = os.path.basename(filename or 'upload')
    if not mimetype or mimetype == 'application/octet-stream':
        mimetype = mimetypes.guess_type(filename)[0] or ''
    if mimetype in IMAGE_TYPES and len(blob) <= MAX_IMAGE:
        kind = 'image'
    elif mimetype in VIDEO_TYPES and len(blob) <= MAX_VIDEO:
        kind = 'video'
    elif mimetype in IMAGE_TYPES | VIDEO_TYPES:
        raise ActionError('That file is too large.', 413)
    else:
        raise ActionError('Only JPEG, PNG, GIF, WebP images and MP4/MOV video are supported.', 415)
    asset_id, url = _send(filename, mimetype, blob)
    result = {'asset_id': asset_id, 'url': url, 'type': kind}
    if kind == 'video':
        _, result['poster_url'] = _send('poster.jpg', 'image/jpeg', video_poster(blob))
    return result
