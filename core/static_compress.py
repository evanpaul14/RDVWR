"""Compress each static file once and serve the stored bytes after that.

flask-compress handles /static itself, but Flask serves files as streams, so it
recompresses the file on every request (hls.min.js is ~620 KB) and can't use gzip for
them at all (its streaming encoders are zstd/br/deflate). On a Raspberry Pi that's real
CPU per visitor. Here each compressible file is compressed at most a couple of times per
version: a background thread does it at the strongest settings at startup, and a request
that arrives first compresses at a fast level so it never waits on that.
"""
import gzip
import logging
import os
import threading

import brotli
from flask import request
from werkzeug.security import safe_join

log = logging.getLogger(__name__)

_EXTENSIONS = ('.js', '.css', '.svg', '.json', '.ico', '.txt', '.xml')
_MIN_SIZE = 1024
_MAX_SIZE = 8 * 1024 * 1024
_MIMETYPES = {'text/javascript', 'application/javascript', 'text/css', 'image/svg+xml',
              'application/json', 'application/manifest+json', 'image/vnd.microsoft.icon',
              'image/x-icon', 'text/plain', 'application/xml', 'text/xml'}

_cache = {}   # (path, encoding) -> (mtime_ns, bytes)
_lock = threading.Lock()


def _compress(data, encoding, best):
    if encoding == 'br':
        return brotli.compress(data, quality=11 if best else 5)
    return gzip.compress(data, compresslevel=9 if best else 6, mtime=0)


def _accepted(accept_encoding):
    """'br' or 'gzip' if the client takes it (and didn't refuse it with q=0), else None."""
    offered = {}
    for part in accept_encoding.lower().split(','):
        name, _, params = part.strip().partition(';')
        q = 1.0
        if params.strip().startswith('q='):
            try:
                q = float(params.strip()[2:])
            except ValueError:
                q = 0.0
        offered[name.strip()] = q
    for enc in ('br', 'gzip'):
        if offered.get(enc, offered.get('*', 0)) > 0:
            return enc
    return None


def _get(path, encoding):
    """The compressed bytes of `path`, or None if it isn't worth compressing."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not (_MIN_SIZE <= st.st_size <= _MAX_SIZE):
        return None
    key = (path, encoding)
    hit = _cache.get(key)
    if hit and hit[0] == st.st_mtime_ns:
        return hit[1]
    with open(path, 'rb') as f:
        body = _compress(f.read(), encoding, best=False)
    with _lock:
        current = _cache.get(key)
        if not current or current[0] != st.st_mtime_ns:
            _cache[key] = (st.st_mtime_ns, body)
    return body


def _warm(folder):
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if not name.endswith(_EXTENSIONS):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
                if not (_MIN_SIZE <= st.st_size <= _MAX_SIZE):
                    continue
                with open(path, 'rb') as f:
                    data = f.read()
                for enc in ('br', 'gzip'):
                    body = _compress(data, enc, best=True)
                    with _lock:
                        _cache[(path, enc)] = (st.st_mtime_ns, body)
            except OSError as e:
                log.warning("static precompress skipped %s: %s", path, e)


def register(app, warm=True):
    folder = app.static_folder

    @app.after_request
    def _compress_static(resp):
        if (request.endpoint != 'static' or resp.status_code != 200
                or 'Content-Encoding' in resp.headers or resp.mimetype not in _MIMETYPES):
            return resp
        filename = (request.view_args or {}).get('filename', '')
        encoding = _accepted(request.headers.get('Accept-Encoding', ''))
        path = safe_join(folder, filename) if filename.endswith(_EXTENSIONS) else None
        body = _get(path, encoding) if (path and encoding) else None
        if body is None:
            return resp
        resp.close()  # the file stream send_file opened
        resp.direct_passthrough = False
        resp.set_data(body)
        resp.headers['Content-Encoding'] = encoding
        vary = resp.headers.get('Vary')
        if not vary:
            resp.headers['Vary'] = 'Accept-Encoding'
        elif 'accept-encoding' not in vary.lower():
            resp.headers['Vary'] = f'{vary}, Accept-Encoding'
        etag, weak = resp.get_etag()
        if etag:
            resp.set_etag(f'{etag}:{encoding}', weak=weak)
            resp.make_conditional(request)
        return resp

    if warm:
        threading.Thread(target=_warm, args=(folder,), name='static-precompress', daemon=True).start()
