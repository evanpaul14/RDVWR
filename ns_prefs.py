"""Visitor preferences for the no-JS (noscript) fallback UI.

The JS app keeps settings in localStorage (static/settings.js), which isn't available
without JS, so the noscript UI stores the same preferences as plain cookies instead,
set by the /settings form (routes/ns_settings.py). Every preference is declared once in
PREFS below; anything not set by the visitor falls back to the deployment-wide
DEFAULT_SETTINGS value, same as settings.js does.
"""
from collections import namedtuple
from flask import request
from helpers import DEFAULT_SETTINGS, SUB_SORTS, TIME_FILTERS, COMMENT_SORTS, THEMES, LAYOUTS

COOKIE_MAX_AGE = 31536000  # 1 year

# cookie:   cookie name
# default:  key into DEFAULT_SETTINGS, or None for an off-by-default preference with no
#           deployment-wide default
# allowed:  set of valid values for an enum preference; None for an on/off preference
Pref = namedtuple('Pref', 'cookie default allowed')

PREFS = {
    'theme':               Pref('ns_theme', 'theme', THEMES),
    'layout':              Pref('ns_layout', 'layout', LAYOUTS),
    'sub_sort':            Pref('ns_sub_sort', 'subSort', SUB_SORTS),
    'sub_time':            Pref('ns_sub_time', 'subTime', TIME_FILTERS),
    'comment_sort':        Pref('ns_comment_sort', 'commentSort', COMMENT_SORTS),
    'link_external_media': Pref('ns_link_external_media', 'linkExternalMedia', None),
    'nsfw_blur':           Pref('ns_nsfw_blur', 'nsfwBlur', None),
    'nsfw_hide':           Pref('ns_nsfw_hide', 'nsfwHide', None),
    'nsfw_search_hide':    Pref('ns_nsfw_search_hide', 'nsfwSearchHide', None),
}


def _default(pref):
    return DEFAULT_SETTINGS[pref.default] if pref.default else False


def get_pref(name):
    """The current request's value for one preference."""
    pref = PREFS[name]
    raw = request.cookies.get(pref.cookie)
    if pref.allowed is None:
        return _default(pref) if raw is None else raw == '1'
    return raw if raw in pref.allowed else _default(pref)


def all_prefs():
    return {name: get_pref(name) for name in PREFS}


def set_pref_cookie(resp, name, value):
    """Store one preference on `resp`. `value` is a bool for on/off preferences or the
    submitted string for enum ones; an invalid enum value clears the cookie (reverting
    to the default)."""
    pref = PREFS[name]
    if pref.allowed is None:
        stored = '1' if value else '0'
    elif value in pref.allowed:
        stored = value
    else:
        resp.delete_cookie(pref.cookie)
        return
    resp.set_cookie(pref.cookie, stored, max_age=COOKIE_MAX_AGE, samesite='Lax')
