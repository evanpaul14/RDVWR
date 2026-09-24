"""Settings for the no-JS (noscript) fallback UI: a plain form that stores each
preference as a cookie (see ns_prefs.py), plus a one-click HLS video toggle link.

A few settings.js settings aren't offered here because they fundamentally need JS: the
personalized home feed / Reddit-cookie login (credentials only ever meant to live
client-side, not round-trip through a server cookie), profile pictures (fetched lazily
per commenter), marking posts read on scroll and clearing read history (both use
localStorage + scroll tracking), and disabling infinite scroll (there's no infinite
scroll here to disable — noscript already paginates via plain "next page" links)."""
from flask import Blueprint, request, render_template, redirect
from helpers import DISABLE_DOWNLOADS
from ns_prefs import PREFS, all_prefs, set_pref_cookie

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

    ctx = {"ns_view": "settings", "page_title": "Settings — RDVWR",
           "page": {"prefs": all_prefs(), "next": _safe_next(request.args.get('next'))}}
    return render_template("index.html", disable_downloads=DISABLE_DOWNLOADS, **ctx), 200, \
        {'Cache-Control': 'no-store'}


@bp.route("/ns-hls")
def toggle_ns_hls():
    """Plain-link toggle for HLS video playback, shown under each video in the noscript view."""
    resp = redirect(_safe_next(request.args.get('next')))
    set_pref_cookie(resp, 'hls', request.args.get('enable') == '1')
    return resp
