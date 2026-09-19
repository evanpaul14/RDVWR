import os
import secrets
import logging
from flask import Flask, g, jsonify, request
from urllib.parse import quote as url_quote
from flask_compress import Compress
from helpers import CACHE_TTL_STATIC, DEFAULT_SETTINGS, DISABLE_PERSONALIZED_HOME, is_same_site_request
from routes import register_all


app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = CACHE_TTL_STATIC
# Route Reddit/Imgur media through /api/m/ instead of letting the browser hit the CDNs.
app.config['PROXY_MEDIA'] = os.environ.get('PROXY_MEDIA', '0') == '1'
Compress(app)


@app.context_processor
def _inject_asset_version():
    def asset_v(filename):
        try:
            return str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
        except OSError:
            return '0'
    def ns_hls_toggle_url(enable):
        return f"/ns-hls?enable={1 if enable else 0}&next={url_quote(request.path, safe='')}"
    return dict(asset_v=asset_v, proxy_media=app.config['PROXY_MEDIA'], csp_nonce=g.get('csp_nonce', ''),
                default_settings=DEFAULT_SETTINGS, disable_personalized_home=DISABLE_PERSONALIZED_HOME,
                ns_hls_toggle_url=ns_hls_toggle_url)


@app.before_request
def _set_csp_nonce():
    g.csp_nonce = secrets.token_urlsafe(16)


# Keep /api/* as the site's own backend, not a public JSON API anyone can hit directly
# (curl, another app, a bot). See helpers.is_same_site_request for what this looks at
# and why. Rate limiting is handled separately at the edge (Vercel Firewall).
@app.before_request
def _gate_api():
    if request.path.startswith('/api/') and not is_same_site_request():
        logging.getLogger(__name__).warning(
            "api gate 403 path=%s sec-fetch-site=%r origin=%r referer=%r host=%r ua=%r",
            request.path, request.headers.get('Sec-Fetch-Site'), request.headers.get('Origin'),
            request.headers.get('Referer'), request.host, request.headers.get('User-Agent'))
        return jsonify({"error": "Forbidden"}), 403
    return None


# Content-Security-Policy: script-src has no 'unsafe-inline' — the handful of
# server-injected inline <script> tags (initial SSR data) carry a fresh
# per-request nonce instead, and app code has no other inline scripts or
# inline event-handler attributes. This is the main defense against exfiltrating
# page state (e.g. localStorage) through an injected <script> if an XSS bug is
# ever found: the browser refuses to run anything not from 'self' or nonced,
# and connect-src 'self' blocks fetch()/XHR exfil to an attacker-controlled host.
# img-src/media-src/frame-src stay open to https: since posts embed arbitrary
# third-party media/oEmbed hosts by design.
# Double-submit cookie for the same-site gate: app.js echoes this back as a request
# header on every fetch() (see app.js), so the gate has a fallback that doesn't depend
# on Sec-Fetch-Site/Origin/Referer, which some browser privacy hardening strips entirely.
@app.after_request
def _set_csrf_cookie(resp):
    if 'rdvwr_csrf' not in request.cookies:
        # Session cookie (no max_age): it only needs to survive one browser session's
        # worth of fetch() calls, not persist as a long-lived per-browser identifier.
        resp.set_cookie('rdvwr_csrf', secrets.token_urlsafe(24), samesite='Lax')
    return resp


@app.after_request
def _set_csp_header(resp):
    nonce = g.get('csp_nonce', '')
    resp.headers['Content-Security-Policy'] = (
        f"default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' https: data:; "
        f"media-src 'self' https: blob:; "
        f"font-src 'self'; "
        f"connect-src 'self'; "
        f"frame-src https:; "
        f"object-src 'none'; "
        f"base-uri 'self'; "
        f"form-action 'self'; "
        f"frame-ancestors 'self'"
    )
    return resp


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("werkzeug").setLevel(logging.WARNING)


register_all(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, threaded=True)
