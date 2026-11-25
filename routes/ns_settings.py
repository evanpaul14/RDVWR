"""Settings for the no-JS (noscript) fallback UI: a plain form that stores each
preference as a cookie (see ns_prefs.py).

A few settings.js settings aren't offered here because they fundamentally need JS:
browser-only Reddit cookies (credentials aren't stored in a preference cookie),
marking posts read on scroll and clearing read history (both use
localStorage + scroll tracking), and disabling infinite scroll (there's no infinite
scroll here to disable — noscript already paginates via plain "next page" links)."""
from flask import Blueprint, request, render_template, redirect
from core.helpers import DISABLE_DOWNLOADS, DISABLE_PERSONALIZED_HOME, NSFW_ONLY_SEARCH, safe_next
from parsing.media_detection import DISABLE_NSFW
from reddit import login as reddit_login
from core.ns_prefs import PREFS, all_prefs, set_pref_cookie
from core.filters import FILTERS, all_filters, set_filter_cookie
from core import backup
from core.base_path import site_root

bp = Blueprint("ns_settings", __name__)


@bp.route("/settings", methods=["GET", "POST"])
def ns_settings():
    if request.method == "POST":
        resp = redirect(safe_next(request.form.get('next')))
        if request.form.get('reset') == '1':
            for pref in (*PREFS.values(), *FILTERS.values()):
                resp.delete_cookie(pref.cookie)
            return resp
        home_available = (not DISABLE_PERSONALIZED_HOME and reddit_login.request_account_active())
        for name, pref in PREFS.items():
            if name in ('personalized_home', 'home_feed') and not (home_available and request.form.get('personalized_home_shown')):
                continue  # the checkbox is only offered while logged in; don't reset it otherwise
            if name in ('nsfw_blur', 'nsfw_hide', 'nsfw_search_hide', 'nsfw_only_search') and DISABLE_NSFW:
                continue
            if name == 'nsfw_only_search' and not NSFW_ONLY_SEARCH:
                continue  # the checkbox is only offered when the deployment opts in
            value = request.form.get(name, '')
            set_pref_cookie(resp, name, value == '1' if pref.allowed is None else value)
        for name in FILTERS:
            set_filter_cookie(resp, name, request.form.get(name, ''))
        return resp

    code = backup.encode(backup.export_current())
    return _render("settings", "Settings", {
        "prefs": all_prefs(), "filters": all_filters(), "login": reddit_login.login_status(),
        "next": safe_next(request.args.get('next')), "imported": request.args.get('import'),
        "backup_code": code, "backup_link": _restore_link(code)})


def _restore_link(code):
    return f"{site_root()}/settings/restore?data={code}"


def _render(view, title, page):
    return render_template("index.html", disable_downloads=DISABLE_DOWNLOADS, ns_view=view,
                           page_title=f"{title} — RDVWR", page=page), 200, {'Cache-Control': 'no-store'}


@bp.route("/settings/restore", methods=["GET", "POST"])
def ns_settings_restore():
    """A restore link (GET) asks before applying anything; the import box on the settings
    page and the confirm button POST here. With JS on, the app handles a restore link
    itself (static/backup.js) since it has more to restore than these cookies."""
    data = backup.decode(request.values.get('data'))
    if request.method == "POST":
        resp = redirect(f"/settings?import={'ok' if data else 'invalid'}")
        if data:
            backup.apply_to_response(resp, data)
        return resp
    prefs, filters = backup.restorable(data) if data else ([], [])
    return _render("restore", "Restore settings", {
        "valid": data is not None, "data": request.args.get('data', ''),
        "prefs": len(prefs), "filters": sum(len(data['settings'][FILTERS[n].key]) for n in filters) if data else 0,
        "subscriptions": len(data['subscriptions']) if data else 0})
