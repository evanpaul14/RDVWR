"""Media download endpoints: single file, gallery zip, and reddit video+audio merge."""
import io
import os
import re
import shutil
import tempfile
import subprocess
import zipfile
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from flask import Blueprint, jsonify, request, Response
from reddit_client import SESSION, HEADERS
from helpers import STREAM_CHUNK_SIZE, DISABLE_DOWNLOADS, error_response, log

bp = Blueprint("downloads", __name__)


def _downloads_enabled(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if DISABLE_DOWNLOADS:
            return jsonify({'error': 'Downloads are disabled'}), 403
        return f(*args, **kwargs)
    return wrapper


def _safe_filename(raw, default):
    name = re.sub(r'[^\w.\-]', '_', raw)[:128]
    return re.sub(r'\.{2,}', '.', name).lstrip('.') or default


def _url_allowed(url, hosts):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ('http', 'https') and parsed.netloc in hosts


def _attachment(filename):
    return f'attachment; filename="{filename}"'


# ── Generic media download proxy ─────────────────────────────────────────────

GALLERY_ALLOWED_HOSTS  = frozenset({'i.redd.it', 'preview.redd.it', 'external-preview.redd.it'})
DOWNLOAD_ALLOWED_HOSTS = GALLERY_ALLOWED_HOSTS | {'v.redd.it', 'i.imgur.com'}
_GALLERY_EXTS = ('jpg', 'jpeg', 'png', 'gif', 'webp', 'mp4')


@bp.route("/api/download")
@_downloads_enabled
def download_media():
    url = request.args.get('url', '').strip()
    filename = _safe_filename(request.args.get('filename', 'media'), 'media')
    if not _url_allowed(url, DOWNLOAD_ALLOWED_HOSTS):
        return jsonify({'error': 'URL not allowed'}), 400
    try:
        upstream = SESSION.get(url, stream=True, timeout=30)
        upstream.raise_for_status()
        resp_headers = {
            'Content-Type': upstream.headers.get('Content-Type', 'application/octet-stream'),
            'Content-Disposition': _attachment(filename),
        }
        if 'Content-Length' in upstream.headers:
            resp_headers['Content-Length'] = upstream.headers['Content-Length']
        return Response(upstream.iter_content(chunk_size=STREAM_CHUNK_SIZE), status=200, headers=resp_headers)
    except Exception:
        return error_response(502)


# ── Gallery zip download ──────────────────────────────────────────────────────

@bp.route("/api/download/gallery")
@_downloads_enabled
def download_gallery():
    urls_param = request.args.get('urls', '').strip()
    name = re.sub(r'[^\w.\-]', '_', request.args.get('name', 'gallery'))
    # Cut long names at the first word break after 20 chars.
    sep = name.find('_', 20)
    if sep != -1:
        name = name[:sep]
    if not urls_param:
        return jsonify({'error': 'No URLs provided'}), 400
    urls = [u.strip() for u in urls_param.split(',') if u.strip()][:25]
    if not all(_url_allowed(url, GALLERY_ALLOWED_HOSTS) for url in urls):
        return jsonify({'error': 'URL not allowed'}), 400

    def _fetch(url):
        try:
            r = SESSION.get(url, stream=True, timeout=20,
                            headers={'Referer': 'https://www.reddit.com/'})
            if not r.ok:
                return None
            ext = urlparse(url).path.rsplit('.', 1)[-1].lower()
            return (ext if ext in _GALLERY_EXTS else 'jpg'), r.content
        except Exception as e:
            log.warning("gallery item download failed host=%s: %s", urlparse(url).hostname, e)
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch, urls))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as zf:
        for i, result in enumerate(results, 1):
            if result is None:
                continue
            ext, content = result
            zf.writestr(f'{name}-{i:02d}.{ext}', content)
    data = buf.getvalue()
    return Response(data, status=200, headers={
        'Content-Type': 'application/zip',
        'Content-Disposition': _attachment(f'{name}-gallery.zip'),
        'Content-Length': str(len(data)),
    })


# ── Reddit video+audio merge download ────────────────────────────────────────

@bp.route("/api/download/reddit-video")
@_downloads_enabled
def download_reddit_video():
    hls_url  = request.args.get('hls', '').strip()
    filename = _safe_filename(request.args.get('filename', 'video.mp4'), 'video.mp4')
    if not _url_allowed(hls_url, {'v.redd.it'}):
        return jsonify({'error': 'URL not allowed'}), 400

    tmpdir = tempfile.mkdtemp()
    out_path = os.path.join(tmpdir, 'merged.mp4')
    try:
        cmd = [
            'ffmpeg', '-y',
            '-user_agent', HEADERS['User-Agent'],
            '-i', hls_url,
            '-c', 'copy',
            '-movflags', '+faststart',
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if result.returncode != 0:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return jsonify({'error': 'ffmpeg failed'}), 502

        size = os.path.getsize(out_path)

        def _stream():
            try:
                with open(out_path, 'rb') as f:
                    while True:
                        chunk = f.read(STREAM_CHUNK_SIZE)
                        if not chunk:
                            break
                        yield chunk
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        return Response(
            _stream(),
            status=200,
            headers={
                'Content-Type': 'video/mp4',
                'Content-Disposition': _attachment(filename),
                'Content-Length': str(size),
            }
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({'error': 'Processing timed out'}), 504
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return error_response(502)
