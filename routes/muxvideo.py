"""Reddit video with sound as one plain MP4, for the no-JS (noscript) view.

Reddit serves video and audio as separate streams; only its HLS playlist carries them
together, and without JS only Safari (and recent Chrome) play HLS natively. This
endpoint remuxes the HLS playlist into a single faststart MP4 with ffmpeg (stream copy,
no re-encode) so any browser's <video> can play it with audio. Results are cached on
disk so the browser's follow-up Range requests (seeking) are served by send_file
instead of re-muxing.
"""
import os
import re
import time
import shutil
import tempfile
import threading
import subprocess
from flask import Blueprint, send_file
from reddit_client import HEADERS
from helpers import log

bp = Blueprint("muxvideo", __name__)

MUX_ENABLED = os.environ.get('DISABLE_VIDEO_MUX', '0') != '1' and shutil.which('ffmpeg') is not None
CACHE_DIR = os.path.join(tempfile.gettempdir(), 'rdvwr-mux')
CACHE_TTL = 3600                      # seconds a muxed file is kept after its last use
CACHE_MAX_BYTES = 1024 * 1024 * 1024  # evict oldest beyond this total
MUX_TIMEOUT = 180

_VID_RE = re.compile(r'^[A-Za-z0-9]{1,20}$')
_HLS_ID_RE = re.compile(r'v\.redd\.it/([A-Za-z0-9]{1,20})/')

_locks = {}
_locks_guard = threading.Lock()


def muxed_video_url(hls_url):
    """The /api/v/<id>.mp4 URL for a post's v.redd.it HLS playlist (raw or proxied
    through /api/m/), or None if it isn't one or muxing is unavailable."""
    if not MUX_ENABLED or not hls_url:
        return None
    m = _HLS_ID_RE.search(hls_url)
    return f"/api/v/{m.group(1)}.mp4" if m else None


def _lock_for(vid):
    with _locks_guard:
        return _locks.setdefault(vid, threading.Lock())


def _evict():
    """Drop cached files unused for CACHE_TTL, then the oldest until under CACHE_MAX_BYTES."""
    try:
        entries = []
        for name in os.listdir(CACHE_DIR):
            path = os.path.join(CACHE_DIR, name)
            st = os.stat(path)
            entries.append((st.st_mtime, st.st_size, path))
    except OSError:
        return
    now = time.time()
    entries.sort()
    total = sum(size for _, size, _ in entries)
    for mtime, size, path in entries:
        if now - mtime < CACHE_TTL and total <= CACHE_MAX_BYTES:
            break
        if path.endswith('.part') and now - mtime < MUX_TIMEOUT:
            continue  # another request's mux still in progress
        try:
            os.remove(path)
            total -= size
        except OSError:
            pass


def _mux(vid, out_path):
    os.makedirs(CACHE_DIR, exist_ok=True)
    _evict()
    part = f"{out_path}.{os.getpid()}.{threading.get_ident()}.part"
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-user_agent', HEADERS['User-Agent'],
        '-i', f'https://v.redd.it/{vid}/HLSPlaylist.m3u8',
        '-c', 'copy',
        '-movflags', '+faststart',
        '-f', 'mp4', part,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=MUX_TIMEOUT)
        if result.returncode != 0:
            log.warning("video mux failed id=%s: %s", vid, result.stderr[-300:])
            return False
        os.replace(part, out_path)
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("video mux failed id=%s: %r", vid, e)
        return False
    finally:
        if os.path.exists(part):
            os.remove(part)


@bp.route("/api/v/<vid>.mp4")
def muxed_video(vid):
    # 404 on any failure so a <video>'s next <source> (HLS / silent MP4) is tried instead
    if not MUX_ENABLED or not _VID_RE.match(vid):
        return '', 404
    out_path = os.path.join(CACHE_DIR, f"{vid}.mp4")
    if not os.path.exists(out_path):
        with _lock_for(vid):
            ok = os.path.exists(out_path) or _mux(vid, out_path)
        with _locks_guard:
            _locks.pop(vid, None)
        if not ok:
            return '', 404
    try:
        os.utime(out_path)  # keep recently watched videos cached
        resp = send_file(out_path, mimetype='video/mp4', conditional=True, max_age=CACHE_TTL)
    except OSError:
        return '', 404  # evicted between the check and the open
    return resp
