"""Visitor content filters: hide posts from chosen subreddits, by chosen users, or whose
title contains a chosen word or phrase.

The JS app keeps the lists in localStorage (static/filters.js, settings.filterSubs /
filterUsers / filterKeywords); the no-JS UI keeps them in cookies set by the /settings
form. The matching rules here mirror static/filters.js, so both UIs hide the same posts:

- A subreddit filter doesn't apply on that subreddit's own page, and a user filter doesn't
  apply on that user's profile, so a filtered community or person is still reachable on
  purpose.
- Keywords match whole words in the title, ignoring case.
"""
import re
from collections import namedtuple
from urllib.parse import quote, unquote
from flask import g, request
from core.ns_prefs import COOKIE_MAX_AGE

MAX_ENTRIES = 100
MAX_KEYWORD_LEN = 100

_SUB_RE = re.compile(r'^[A-Za-z0-9_]{1,50}$')
_USER_RE = re.compile(r'^[A-Za-z0-9_-]{1,30}$')


def _dedupe(items):
    seen, out = set(), []
    for it in items:
        if it.lower() not in seen:
            seen.add(it.lower())
            out.append(it)
    return out[:MAX_ENTRIES]


def _names(text, prefix_re, valid_re):
    items = re.split(r'[\s,]+', text or '')
    items = [prefix_re.sub('', it) for it in items]
    return _dedupe([it for it in items if valid_re.match(it)])


def normalize_subs(value):
    """Subreddit names from a string (comma/whitespace separated) or a list."""
    text = '\n'.join(value) if isinstance(value, (list, tuple)) else value
    return _names(text, re.compile(r'^/?r/', re.I), _SUB_RE)


def normalize_users(value):
    text = '\n'.join(value) if isinstance(value, (list, tuple)) else value
    return _names(text, re.compile(r'^/?u(?:ser)?/', re.I), _USER_RE)


def normalize_keywords(value):
    """Keywords/phrases, one per line (a phrase may contain spaces and commas)."""
    items = value if isinstance(value, (list, tuple)) else (value or '').split('\n')
    items = [' '.join(str(it).split())[:MAX_KEYWORD_LEN] for it in items if isinstance(it, str)]
    return _dedupe([it for it in items if it])


# name: form field / template name; cookie; key: the settings.js key; normalize
Filter = namedtuple('Filter', 'cookie key normalize')

FILTERS = {
    'filter_subs':     Filter('ns_filter_subs', 'filterSubs', normalize_subs),
    'filter_users':    Filter('ns_filter_users', 'filterUsers', normalize_users),
    'filter_keywords': Filter('ns_filter_keywords', 'filterKeywords', normalize_keywords),
}


def get_filter(name):
    raw = request.cookies.get(FILTERS[name].cookie)
    return FILTERS[name].normalize(unquote(raw).split('\n')) if raw else []


def all_filters():
    return {name: get_filter(name) for name in FILTERS}


def set_filter_cookie(resp, name, value):
    """Store one filter list (a string as typed in the form, or a list) on `resp`; an
    empty list clears the cookie."""
    f = FILTERS[name]
    items = f.normalize(value)
    if not items:
        resp.delete_cookie(f.cookie)
        return
    resp.set_cookie(f.cookie, quote('\n'.join(items), safe=''), max_age=COOKIE_MAX_AGE, samesite='Lax')


def keyword_matches(keyword, title):
    return re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', title or '', re.I) is not None


_PAGE_SUB_RE = re.compile(r'^/r/([^/+]+)(?:/|$)', re.I)
_PAGE_USER_RE = re.compile(r'^/u(?:ser)?/([^/]+)(?:/|$)', re.I)


def post_is_filtered(p, filters, path):
    """Whether post `p` is hidden by `filters` (all_filters()) on the page at `path`."""
    sub = (p.get('subreddit') or '').lower()
    author = (p.get('author') or '').lower()
    m = _PAGE_SUB_RE.match(path)
    page_sub = m.group(1).lower() if m else None
    m = _PAGE_USER_RE.match(path)
    page_user = m.group(1).lower() if m else None
    if sub != page_sub and any(s.lower() == sub for s in filters['filter_subs']):
        return True
    if author != page_user and any(u.lower() == author for u in filters['filter_users']):
        return True
    return any(keyword_matches(k, p.get('title')) for k in filters['filter_keywords'])


def post_hidden(p):
    """Template helper: whether the current visitor's filters hide post `p` here."""
    if request.path.startswith(('/account', '/saved')):
        return False
    filters = g.get('_visitor_filters')
    if filters is None:
        filters = g._visitor_filters = all_filters()
    if not any(filters.values()):
        return False
    return post_is_filtered(p, filters, request.path)
