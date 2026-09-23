"""Settings page for the no-JS (noscript) fallback UI. localStorage (what the JS
app's settings.js uses) isn't available without JS, so preferences here are
plain cookies set via a normal POST form instead.

A few settings.js settings aren't offered here because they fundamentally need
JS: the personalized home feed / Reddit-cookie login (credentials only ever meant
to live client-side, not round-trip through a server cookie), marking posts read
on scroll and clearing read history (both use localStorage + scroll tracking),
and disabling infinite scroll (there's no infinite scroll here to disable —
noscript already paginates via plain "next page" links)."""
from flask import Blueprint, request, render_template, make_response, redirect
from helpers import DISABLE_DOWNLOADS, FEED_SORTS, TIME_FILTERS, NS_COMMENT_SORTS, ns_cookie_bool, ns_cookie_enum, ns_context

bp = Blueprint("ns_settings", __name__)

_COOKIE_MAX_AGE = 31536000  # 1 year

_THEME_OPTS    = {'dark', 'light', 'system'}
_LAYOUT_OPTS   = {'card', 'compact', 'minimal'}
_SUB_SORT_OPTS = FEED_SORTS - {'best'}


def _safe_next(raw):
    if not raw or not raw.startswith('/') or raw.startswith('//'):
        return '/'
    return raw


def _set_bool_cookie(resp, name, value):
    if value:
        resp.set_cookie(name, '1', max_age=_COOKIE_MAX_AGE, samesite='Lax')
    else:
        resp.delete_cookie(name)


def _set_enum_cookie(resp, name, value, allowed):
    if value in allowed:
        resp.set_cookie(name, value, max_age=_COOKIE_MAX_AGE, samesite='Lax')
    else:
        resp.delete_cookie(name)


@bp.route("/settings", methods=["GET", "POST"])
def ns_settings():
    if request.method == "POST":
        next_path = _safe_next(request.form.get('next'))
        resp = make_response(redirect(next_path))
        _set_bool_cookie(resp, 'ns_nsfw_blur', request.form.get('ns_nsfw_blur') == '1')
        _set_bool_cookie(resp, 'ns_nsfw_hide', request.form.get('ns_nsfw_hide') == '1')
        _set_bool_cookie(resp, 'ns_nsfw_search_hide', request.form.get('ns_nsfw_search_hide') == '1')
        _set_bool_cookie(resp, 'ns_hls', request.form.get('ns_hls') == '1')
        _set_bool_cookie(resp, 'ns_link_external_media', request.form.get('ns_link_external_media') == '1')
        _set_enum_cookie(resp, 'ns_theme', request.form.get('ns_theme', ''), _THEME_OPTS)
        _set_enum_cookie(resp, 'ns_layout', request.form.get('ns_layout', ''), _LAYOUT_OPTS)
        _set_enum_cookie(resp, 'ns_sub_sort', request.form.get('ns_sub_sort', ''), _SUB_SORT_OPTS)
        _set_enum_cookie(resp, 'ns_sub_time', request.form.get('ns_sub_time', ''), TIME_FILTERS)
        _set_enum_cookie(resp, 'ns_comment_sort', request.form.get('ns_comment_sort', ''), NS_COMMENT_SORTS)
        return resp

    next_path = _safe_next(request.args.get('next'))
    ns_settings_data = {
        'nsfw_blur':          ns_cookie_bool('ns_nsfw_blur', 'nsfwBlur'),
        'nsfw_hide':          ns_cookie_bool('ns_nsfw_hide', 'nsfwHide'),
        'nsfw_search_hide':   ns_cookie_bool('ns_nsfw_search_hide', 'nsfwSearchHide'),
        'hls':                request.cookies.get('ns_hls') == '1',
        'link_external_media': ns_cookie_bool('ns_link_external_media', 'linkExternalMedia'),
        'theme':              ns_cookie_enum('ns_theme', 'theme', _THEME_OPTS),
        'layout':             ns_cookie_enum('ns_layout', 'layout', _LAYOUT_OPTS),
        'sub_sort':           ns_cookie_enum('ns_sub_sort', 'subSort', _SUB_SORT_OPTS),
        'sub_time':           ns_cookie_enum('ns_sub_time', 'subTime', TIME_FILTERS),
        'comment_sort':       ns_cookie_enum('ns_comment_sort', 'commentSort', NS_COMMENT_SORTS),
        'next': next_path,
    }
    resp = render_template("index.html", ns_settings=ns_settings_data, disable_downloads=DISABLE_DOWNLOADS, **ns_context())
    return resp, 200, {'Cache-Control': 'no-store'}
