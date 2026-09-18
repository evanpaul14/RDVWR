"""Media download endpoints: single file, gallery zip, and reddit video+audio merge."""
import os
import re
import shutil
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from flask import Blueprint, jsonify, request, Response
from reddit_client import SESSION, HEADERS
from helpers import STREAM_CHUNK_SIZE

bp = Blueprint("downloads", __name__)


# ── Generic media download proxy ─────────────────────────────────────────────

DOWNLOAD_ALLOWED_HOSTS = frozenset({
    'v.redd.it',
    'i.redd.it',
    'preview.redd.it',
    'external-preview.redd.it',
    'i.imgur.com',
})


@bp.route("/api/download")
def download_media():
    url = request.args.get('url', '').strip()
    filename = re.sub(r'[^\w.\-]', '_', request.args.get('filename', 'media'))[:128]
    filename = re.sub(r'\.{2,}', '.', filename).lstrip('.') or 'media'
    try:
        parsed = urlparse(url)
    except Exception:
        return jsonify({'error': 'Invalid URL'}), 400
    if parsed.scheme not in ('http', 'https') or parsed.netloc not in DOWNLOAD_ALLOWED_HOSTS:
        return jsonify({'error': 'URL not allowed'}), 400
    try:
        upstream = SESSION.get(url, stream=True, timeout=30)
        upstream.raise_for_status()
        content_type = upstream.headers.get('Content-Type', 'application/octet-stream')
        resp_headers = {
            'Content-Type': content_type,
            'Content-Disposition': f'attachment; filename="{filename}"',
        }
        if 'Content-Length' in upstream.headers:
            resp_headers['Content-Length'] = upstream.headers['Content-Length']
        return Response(upstream.iter_content(chunk_size=STREAM_CHUNK_SIZE), status=200, headers=resp_headers)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ── Gallery zip download ──────────────────────────────────────────────────────

GALLERY_ALLOWED_HOSTS = frozenset({'i.redd.it', 'preview.redd.it', 'external-preview.redd.it'})

@bp.route("/api/download/gallery")
def download_gallery():
    import io, zipfile

    urls_param = request.args.get('urls', '').strip()
    _raw = re.sub(r'[^\w.\-]', '_', request.args.get('name', 'gallery'))
    if len(_raw) > 20:
        _sep = _raw.find('_', 20)
        name = _raw[:_sep] if _sep != -1 else _raw
    else:
        name = _raw
    if not urls_param:
        return jsonify({'error': 'No URLs provided'}), 400
    urls = [u.strip() for u in urls_param.split(',') if u.strip()][:25]
    for url in urls:
        try:
            parsed = urlparse(url)
        except Exception:
            return jsonify({'error': 'Invalid URL'}), 400
        if parsed.scheme not in ('http', 'https') or parsed.netloc not in GALLERY_ALLOWED_HOSTS:
            return jsonify({'error': 'URL not allowed'}), 400

    def _fetch(url):
        try:
            r = SESSION.get(url, stream=True, timeout=20,
                            headers={'Referer': 'https://www.reddit.com/'})
            if not r.ok:
                return None
            path = urlparse(url).path
            ext = path.rsplit('.', 1)[-1].lower() if '.' in path else 'jpg'
            if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'mp4'):
                ext = 'jpg'
            return ext, r.content
        except Exception:
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
    buf.seek(0)
    data = buf.read()
    return Response(data, status=200, headers={
        'Content-Type': 'application/zip',
        'Content-Disposition': f'attachment; filename="{name}-gallery.zip"',
        'Content-Length': str(len(data)),
    })


# ── Reddit video+audio merge download ────────────────────────────────────────

@bp.route("/api/download/reddit-video")
def download_reddit_video():
    hls_url  = request.args.get('hls', '').strip()
    filename = re.sub(r'[^\w.\-]', '_', request.args.get('filename', 'video.mp4'))[:128]

    try:
        parsed = urlparse(hls_url)
    except Exception:
        return jsonify({'error': 'Invalid URL'}), 400
    if parsed.scheme not in ('http', 'https') or parsed.netloc != 'v.redd.it':
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
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(size),
            }
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({'error': 'Processing timed out'}), 504
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({'error': str(e)}), 502
