"""Atom feeds for subreddits and user profiles, at the same paths Reddit uses:
/r/<sub>.rss, /r/<sub>/<sort>.rss (?t= for top/controversial, ?quarantine_opt_in=1 for a quarantined
subreddit), /user/<name>.rss (also
/u/<name>.rss and /user/<name>/submitted.rss). Entries link to this app's post pages.

Like Reddit's own ".rss" URLs these are Atom, not RSS 2.0; every feed reader takes both.
"""
import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr
from flask import Blueprint, Response, request
from core.base_path import site_root
from core.config import FEED_SORTS, TIME_FILTERS
from core.helpers import UpstreamError, log
from core.template_helpers import mdbody
from routes.subreddit import fetch_feed
from routes.users import fetch_user_posts

bp = Blueprint("rss", __name__)

_SUB_RSS_RE = re.compile(r'^/r/([A-Za-z0-9_+]{1,100})(?:/(best|hot|new|top|rising|controversial))?/?\.rss$')
_USER_RSS_RE = re.compile(r'^/u(?:ser)?/([A-Za-z0-9_-]{1,50})(?:/submitted)?/?\.rss$')
_REL_URL_RE = re.compile(r'(\b(?:href|src)=")/(?!/)')
CACHE_SECS = 300


def _iso(ts):
    return datetime.fromtimestamp(ts or 0, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _entry_html(p, root):
    parts = []
    if p.get('preview_img'):
        src = p['preview_img']
        if src.startswith('/'):
            src = root + src
        parts.append(f'<p><img src={quoteattr(src)} alt=""></p>')
    if p.get('is_self'):
        body = str(mdbody(p.get('selftext'), p.get('selftext_html')))
        parts.append(_REL_URL_RE.sub(rf'\1{root}/', body))
    elif p.get('url'):
        parts.append(f'<p><a href={quoteattr(p["url"])}>{escape(p.get("domain") or p["url"])}</a></p>')
    meta = (f'submitted by <a href={quoteattr(f"{root}/user/{p.get("author")}")}>u/{escape(p.get("author") or "")}</a>'
            f' to <a href={quoteattr(f"{root}/r/{p.get("subreddit")}")}>r/{escape(p.get("subreddit") or "")}</a>'
            f' · {p.get("score", 0)} points · {p.get("num_comments", 0)} comments')
    parts.append(f'<p>{meta}</p>')
    return ''.join(parts)


def _atom(title, self_url, html_url, posts):
    root = site_root()
    updated = max((p.get('created_utc') or 0 for p in posts), default=0)
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">',
           f'<title>{escape(title)}</title>',
           f'<id>{escape(self_url)}</id>',
           f'<link rel="self" type="application/atom+xml" href={quoteattr(self_url)}/>',
           f'<link rel="alternate" type="text/html" href={quoteattr(html_url)}/>',
           f'<updated>{_iso(updated)}</updated>',
           '<generator>RDVWR</generator>']
    for p in posts:
        link = f"{root}/r/{p.get('subreddit')}/comments/{p.get('id')}"
        out.append(
            '<entry>'
            f'<title>{escape(p.get("title") or "")}</title>'
            f'<id>{escape(link)}</id>'
            f'<link rel="alternate" type="text/html" href={quoteattr(link)}/>'
            f'<published>{_iso(p.get("created_utc"))}</published>'
            f'<updated>{_iso(p.get("edited_utc") or p.get("created_utc"))}</updated>'
            f'<author><name>u/{escape(p.get("author") or "")}</name><uri>{escape(f"{root}/user/{p.get("author")}")}</uri></author>'
            f'<category term={quoteattr(p.get("subreddit") or "")} label={quoteattr("r/" + (p.get("subreddit") or ""))}/>'
            f'<content type="html">{escape(_entry_html(p, root))}</content>'
            '</entry>')
    out.append('</feed>')
    return '\n'.join(out)


def _error(e):
    return Response(f"{e.message}\n", status=e.status if e.status in (403, 404) else 502, mimetype='text/plain')


@bp.before_app_request
def _rss():
    """Runs ahead of routing (like passthrough.py's ".json"), so a ".rss" URL never falls
    into the HTML page routes that would also match it."""
    path = request.path
    if not path.endswith('.rss'):
        return None
    root = site_root()
    try:
        if m := _SUB_RSS_RE.match(path):
            sub, sort = m.group(1), m.group(2) or 'hot'
            t = request.args.get('t', '')
            t = t if t in TIME_FILTERS else ''
            if sort not in FEED_SORTS:
                sort = 'hot'
            opt_in = request.args.get('quarantine_opt_in') == '1'
            posts = fetch_feed(sub, sort, t, '', opt_in)["posts"]
            title = f"r/{sub}" + (f" — {sort}" if sort != 'hot' else '')
            html_url = f"{root}/r/{sub}" + (f"/{sort}" if m.group(2) else '') + (f"?t={t}" if t else '')
        elif m := _USER_RSS_RE.match(path):
            user = m.group(1)
            posts = fetch_user_posts(user)["posts"]
            title, html_url = f"u/{user}", f"{root}/user/{user}"
        else:
            return Response("not found\n", status=404, mimetype='text/plain')
    except UpstreamError as e:
        return _error(e)
    except Exception as e:
        log.warning("rss feed failed path=%s: %s", path, e)
        return Response("upstream request failed\n", status=502, mimetype='text/plain')
    self_url = root + path + (f"?{request.query_string.decode()}" if request.query_string else '')
    return Response(_atom(title, self_url, html_url, posts), mimetype='application/atom+xml',
                    headers={'Cache-Control': f'public, max-age={CACHE_SECS}'})
