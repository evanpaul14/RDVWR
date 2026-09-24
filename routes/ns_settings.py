"""No-JS settings form; each preference is stored as a cookie (see ns_prefs.py).
Settings that need JS (Reddit cookies, avatars, read tracking, infinite scroll) are omitted."""
from flask import Blueprint, request, redirect
from ns_prefs import PREFS, all_prefs, set_pref_cookie
from routes.pages import respond

bp = Blueprint("ns_settings", __name__)


def _safe_next(raw):
    """Only ever redirect back to a same-site path."""
    if not raw or not raw.startswith('/') or raw.startswith('//') or '\\' in raw:
        return '/'
    return raw


@bp.route("/settings", methods=["GET", "POST"])
def ns_settings():
    if request.method == "POST":
        resp = redirect(_safe_next(request.form.get('next')))
        for name, pref in PREFS.items():
            value = request.form.get(name, '')
            set_pref_cookie(resp, name, value == '1' if pref.allowed is None else value)
        return resp

    return respond({"ns_view": "settings", "page_title": "Settings — RDVWR",
                    "page": {"prefs": all_prefs(), "next": _safe_next(request.args.get('next'))}})
