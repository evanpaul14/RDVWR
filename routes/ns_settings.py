"""Settings for the no-JS (noscript) fallback UI: a plain form that stores each
preference as a cookie (see ns_prefs.py).

A few settings.js settings aren't offered here because they fundamentally need JS: the
personalized home feed / Reddit-cookie login (credentials only ever meant to live
client-side, not round-trip through a server cookie), profile pictures (fetched lazily
per commenter), marking posts read on scroll and clearing read history (both use
localStorage + scroll tracking), and disabling infinite scroll (there's no infinite
scroll here to disable — noscript already paginates via plain "next page" links)."""
from flask import Blueprint, request, render_template, redirect
from helpers import DISABLE_DOWNLOADS, safe_next
import reddit_login
from ns_prefs import PREFS, all_prefs, set_pref_cookie

bp = Blueprint("ns_settings", __name__)


@bp.route("/settings", methods=["GET", "POST"])
def ns_settings():
    if request.method == "POST":
        resp = redirect(safe_next(request.form.get('next')))
        for name, pref in PREFS.items():
            if name == 'personalized_home' and not request.form.get('personalized_home_shown'):
                continue  # the checkbox is only offered while logged in; don't reset it otherwise
            value = request.form.get(name, '')
            set_pref_cookie(resp, name, value == '1' if pref.allowed is None else value)
        return resp

    ctx = {"ns_view": "settings", "page_title": "Settings — RDVWR",
           "page": {"prefs": all_prefs(), "login": reddit_login.login_status(), "next": safe_next(request.args.get('next'))}}
    return render_template("index.html", disable_downloads=DISABLE_DOWNLOADS, **ctx), 200, \
        {'Cache-Control': 'no-store'}
