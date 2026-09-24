"""No-JS visitor preferences, stored as cookies (set by the /settings form).
Unset preferences fall back to DEFAULT_SETTINGS, like settings.js."""
from collections import namedtuple
from flask import request
from helpers import DEFAULT_SETTINGS, SUB_SORTS, TIME_FILTERS, COMMENT_SORTS, THEMES, LAYOUTS

COOKIE_MAX_AGE = 31536000  # 1 year

# default: DEFAULT_SETTINGS key (None = off). allowed: enum values (None = on/off).
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
    pref = PREFS[name]
    raw = request.cookies.get(pref.cookie)
    if pref.allowed is None:
        return _default(pref) if raw is None else raw == '1'
    return raw if raw in pref.allowed else _default(pref)


def all_prefs():
    return {name: get_pref(name) for name in PREFS}


def set_pref_cookie(resp, name, value):
    """Store a preference; an invalid enum value clears the cookie (back to default)."""
    pref = PREFS[name]
    if pref.allowed is None:
        stored = '1' if value else '0'
    elif value in pref.allowed:
        stored = value
    else:
        resp.delete_cookie(pref.cookie)
        return
    resp.set_cookie(pref.cookie, stored, max_age=COOKIE_MAX_AGE, samesite='Lax')
