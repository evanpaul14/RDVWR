import os
import secrets
import logging
from flask import Flask, g, jsonify, request
from flask_compress import Compress
import template_helpers
from helpers import CACHE_TTL_STATIC, is_same_site_request
from routes import register_all


app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = CACHE_TTL_STATIC
# Route Reddit/Imgur media through /api/m/ instead of letting the browser hit the CDNs.
app.config['PROXY_MEDIA'] = os.environ.get('PROXY_MEDIA', '0') == '1'
Compress(app)
template_helpers.register(app)

log = logging.getLogger(__name__)

# Loaded by <img>/<video>/navigation, so they can't send the X-Rdvwr-Fetch header.
# Safe to exempt: they only serve allowlisted public media with no state or session data.
_GATE_EXEMPT_PREFIXES = ('/api/img', '/api/m/', '/api/redgifs/media/', '/api/download')

# XSS containment: only 'self' or nonced scripts run, and connect-src blocks exfil.
# img/media/frame stay open to https: since posts embed arbitrary third-party media.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' https: data:; "
    "media-src 'self' https: blob:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-src https:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)


@app.before_request
def _set_csp_nonce():
    g.csp_nonce = secrets.token_urlsafe(16)


@app.before_request
def _gate_api():
    """Keep /api/* private to this site's own frontend (see is_same_site_request)."""
    if (request.path.startswith('/api/') and not request.path.startswith(_GATE_EXEMPT_PREFIXES)
            and not is_same_site_request()):
        log.warning(
            "api gate 403 path=%s sec-fetch-site=%r origin=%r referer=%r host=%r ua=%r",
            request.path, request.headers.get('Sec-Fetch-Site'), request.headers.get('Origin'),
            request.headers.get('Referer'), request.host, request.headers.get('User-Agent'))
        return jsonify({"error": "Forbidden"}), 403
    return None


@app.after_request
def _set_csrf_cookie(resp):
    # Double-submit token app.js echoes as X-Rdvwr-Fetch; session-only so it isn't a tracker.
    if 'rdvwr_csrf' not in request.cookies:
        resp.set_cookie('rdvwr_csrf', secrets.token_urlsafe(24), samesite='Lax')
    return resp


@app.after_request
def _set_csp_header(resp):
    resp.headers['Content-Security-Policy'] = _CSP.format(nonce=g.get('csp_nonce', ''))
    return resp


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

register_all(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, threaded=True)
