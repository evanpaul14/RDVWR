"""Settings page for the no-JS (noscript) fallback UI. localStorage (what the JS
app's settings.js uses) isn't available without JS, so preferences here are
plain cookies set via a normal POST form instead."""
from flask import Blueprint, request, render_template, make_response, redirect
from helpers import DISABLE_DOWNLOADS

bp = Blueprint("ns_settings", __name__)

_COOKIE_MAX_AGE = 31536000  # 1 year


def _safe_next(raw):
    if not raw or not raw.startswith('/') or raw.startswith('//'):
        return '/'
    return raw


def _set_bool_cookie(resp, name, value):
    if value:
        resp.set_cookie(name, '1', max_age=_COOKIE_MAX_AGE, samesite='Lax')
    else:
        resp.delete_cookie(name)


@bp.route("/settings", methods=["GET", "POST"])
def ns_settings():
    if request.method == "POST":
        next_path = _safe_next(request.form.get('next'))
        resp = make_response(redirect(next_path))
        _set_bool_cookie(resp, 'ns_nsfw_blur', request.form.get('ns_nsfw_blur') == '1')
        _set_bool_cookie(resp, 'ns_hls', request.form.get('ns_hls') == '1')
        return resp

    next_path = _safe_next(request.args.get('next'))
    ns_settings_data = {
        'nsfw_blur': request.cookies.get('ns_nsfw_blur') == '1',
        'hls': request.cookies.get('ns_hls') == '1',
        'next': next_path,
    }
    resp = render_template("index.html", ns_settings=ns_settings_data, disable_downloads=DISABLE_DOWNLOADS)
    return resp, 200, {'Cache-Control': 'no-store'}
