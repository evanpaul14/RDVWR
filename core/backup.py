"""Settings backup codes: a visitor's settings as one copyable string, also usable as a
link (/settings/restore?data=<code>) that restores them on another browser or device.

The code is base64url-encoded JSON, {"v": 1, "settings": {...}, "subscriptions": [...]},
with settings under their settings.js names. static/backup.js reads and writes the
same format; the no-JS UI exports and restores the subset it has as cookies (ns_prefs
preferences and filters; local subscriptions need JS, so it carries but can't apply them).

Never included: pasted Reddit cookies or the server-side login, saved posts, read history.
"""
import base64
import binascii
import json
from urllib.parse import urlsplit, parse_qs
from core.ns_prefs import PREFS, get_pref, set_pref_cookie
from core.filters import FILTERS, get_filter, set_filter_cookie

VERSION = 1
MAX_CODE_LEN = 20000


def encode(data):
    raw = json.dumps(data, separators=(',', ':'), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode(text):
    """The backup dict from a code or a restore link, or None if it isn't valid."""
    text = (text or '').strip()
    if '?' in text or text.startswith(('http:', 'https:', '/')):
        text = (parse_qs(urlsplit(text).query).get('data') or [''])[0]
    if not text or len(text) > MAX_CODE_LEN:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(text + '=' * (-len(text) % 4)))
    except (binascii.Error, ValueError):
        return None
    if not isinstance(data, dict) or data.get('v') != VERSION or not isinstance(data.get('settings', {}), dict):
        return None
    subs = data.get('subscriptions', [])
    if not isinstance(subs, list):
        return None
    return {'v': VERSION, 'settings': data.get('settings', {}),
            'subscriptions': [s for s in subs if isinstance(s, str)]}


def export_current():
    """The current request's no-JS settings (cookies, falling back to defaults)."""
    settings = {pref.default: get_pref(name) for name, pref in PREFS.items()}
    settings.update({f.key: get_filter(name) for name, f in FILTERS.items()})
    return {'v': VERSION, 'settings': settings}


def _valid(pref, value):
    return isinstance(value, bool) if pref.allowed is None else value in pref.allowed


def restorable(data):
    """(preference names, filter names) in `data` that the no-JS UI can restore."""
    settings = data['settings']
    prefs = [name for name, pref in PREFS.items() if pref.default in settings and _valid(pref, settings[pref.default])]
    filters = [name for name, f in FILTERS.items() if isinstance(settings.get(f.key), list)]
    return prefs, filters


def apply_to_response(resp, data):
    """Set the no-JS preference and filter cookies from `data` on `resp`."""
    settings = data['settings']
    prefs, filters = restorable(data)
    for name in prefs:
        set_pref_cookie(resp, name, settings[PREFS[name].default])
    for name in filters:
        set_filter_cookie(resp, name, settings[FILTERS[name].key])
