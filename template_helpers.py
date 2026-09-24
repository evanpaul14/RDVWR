"""Jinja filters and globals for templates/ — mostly Python ports of the static/utils.js
formatters, so the server-rendered noscript view formats things the same way the JS
app does."""
import os
import re
import time
import datetime
from urllib.parse import urlencode, urlparse, parse_qsl
from flask import g, request
from markupsafe import Markup
from helpers import DEFAULT_SETTINGS, DISABLE_DOWNLOADS, DISABLE_PERSONALIZED_HOME
from reddit_html import EXTERNAL_MEDIA_CLASS
from ns_prefs import all_prefs, get_pref


def timeago(utc):
    """static/utils.js timeAgo()."""
    if not utc:
        return ''
    s = int(time.time()) - int(utc)
    if s < 60:       return f"{s}s"
    if s < 3600:     return f"{s // 60}m"
    if s < 86400:    return f"{s // 3600}h"
    if s < 2592000:  return f"{s // 86400}d"
    if s < 31536000: return f"{s // 2592000}mo"
    return f"{s // 31536000}y"


def fmtnum(n):
    """static/utils.js fmtNum()."""
    n = n or 0
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.1f}K"
    return str(n)


def fmtdate(utc):
    """static/utils.js fmtDate()."""
    if not utc:
        return ''
    return datetime.datetime.fromtimestamp(int(utc), datetime.timezone.utc).strftime('%b %-d, %Y')


def fmtdatetime(utc):
    """Full timestamp for a tooltip, like static/utils.js fmtDateTime()."""
    if not utc:
        return ''
    return datetime.datetime.fromtimestamp(int(utc), datetime.timezone.utc).strftime('%b %-d, %Y, %H:%M UTC')


def usable_bg(hex_color):
    """static/utils.js isUsableBg(): filters out unset/transparent/too-light flair
    background colors."""
    if not hex_color or hex_color == 'transparent':
        return False
    m = re.match(r'^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$', hex_color, re.I)
    if not m:
        return False
    r, g_, b = (int(x, 16) for x in m.groups())
    return 0.299 * r + 0.587 * g_ + 0.114 * b < 180


# Matches render.js's bot detection for auto-collapsing comments: "bot" at the end of
# the name, at its start, or next to a separator/digit (so not e.g. "Robotics").
_BOT_NAME_RE = re.compile(r'(?:^|[_\-\d])bot(?:[_\-\d]|$)|bot$', re.I)


def is_bot(author):
    return bool(author and _BOT_NAME_RE.search(author))


_EXT_MEDIA_IMG_RE = re.compile(rf'(<a class="{EXTERNAL_MEDIA_CLASS}"[^>]*>)<img [^>]*></a>')


def md(sanitized_html):
    """Render one of the sanitized *_html fields (media_detection.sanitize_reddit_html).
    Third-party inline media in it becomes a plain link when the visitor has "don't embed
    third-party media" on, like the JS markdown renderer's _asLink."""
    html = sanitized_html or ''
    if get_pref('link_external_media'):
        html = _EXT_MEDIA_IMG_RE.sub(r'\1gif &#8599;</a>', html)
    return Markup(html)


# ── Post classification (ports of render.js / postview.js checks) ────────────

_EMBED_KEYS = ('is_video', 'youtube_id', 'tiktok_id', 'redgifs_id', 'imgur_album_id', 'streamable_id', 'embed_url')
_IMAGE_DOMAIN_RE = re.compile(r'^i\.\w')


def link_domain(p):
    """The outside site a link post points to, for the footer's domain link (render.js
    domainHtml), or None for self posts and Reddit-hosted media."""
    domain = p.get('domain') or ''
    url = p.get('url') or ''
    if p.get('is_self') or not domain or domain.startswith('self.') or domain.endswith('redd.it') \
            or '/gallery/' in url or 'v.redd.it' in url:
        return None
    return domain


def is_article(p):
    """A link to an outside page rather than media this site can show inline; the card
    layout draws these as a compact row with a thumbnail (render.js isCompact)."""
    if p.get('is_self') or p.get('crosspost_from') or p.get('linked_post') or not p.get('url'):
        return False
    if any(p.get(k) for k in _EMBED_KEYS) or p.get('gif_url') or len(p.get('gallery') or []) > 1:
        return False
    return not _IMAGE_DOMAIN_RE.match(p.get('domain') or '')


def article_url(p):
    """The outbound link shown in the post view's article box (postview.js isArticle)."""
    if not link_domain(p) or p.get('crosspost_from') or p.get('linked_post') \
            or any(p.get(k) for k in _EMBED_KEYS if k != 'imgur_album_id') \
            or (p.get('gif_url') and p.get('gif_is_video')):
        return None
    return p['url']


# ── Downloads (mirrors buildDownloadBtn in static/postview.js) ────────────────

_DL_HOSTS = frozenset({'v.redd.it', 'i.redd.it', 'preview.redd.it', 'external-preview.redd.it', 'i.imgur.com'})
_GALLERY_DL_HOSTS = frozenset({'i.redd.it', 'preview.redd.it', 'external-preview.redd.it'})
_IMG_EXTS = ('jpg', 'jpeg', 'png', 'gif', 'webp')


def _host_in(url, hosts):
    return bool(url) and urlparse(url).hostname in hosts


def download_url(p):
    """A plain-link download URL for a post's media, or None if it has none this site
    can download (or downloads are disabled). Redgifs and Imgur-album downloads need
    JS to resolve the media first, so they aren't offered."""
    if DISABLE_DOWNLOADS or p.get('redgifs_id') or p.get('imgur_album_id'):
        return None
    if p.get('gallery'):
        urls = [img['url'] for img in p['gallery'] if _host_in(img.get('url'), _GALLERY_DL_HOSTS)][:25]
        return '/api/download/gallery?' + urlencode({'urls': ','.join(urls), 'name': p['id']}) if urls else None
    if p.get('is_video') and p.get('video_url') and p.get('hls_url'):
        return '/api/download/reddit-video?' + urlencode({'hls': p['hls_url'], 'filename': f"{p['id']}.mp4"})
    if p.get('is_video') and p.get('video_url'):
        url, filename = p['video_url'], f"{p['id']}.mp4"
    elif p.get('gif_url'):
        url, filename = p['gif_url'], f"{p['id']}.{'mp4' if p.get('gif_is_video') else 'gif'}"
    elif not any(p.get(k) for k in ('youtube_id', 'tiktok_id', 'streamable_id', 'embed_url', 'is_self')) \
            and p.get('preview_img'):
        url = p['preview_img']
        ext = url.split('?')[0].rsplit('.', 1)[-1].lower()
        filename = f"{p['id']}.{ext if ext in _IMG_EXTS else 'jpg'}"
    else:
        return None
    if not _host_in(url, _DL_HOSTS):
        return None
    return '/api/download?' + urlencode({'url': url, 'filename': filename})


# ── Request-dependent globals ─────────────────────────────────────────────────

def current_url():
    """This request's path + query, for "come back here afterwards" links."""
    qs = request.query_string.decode()
    return request.path + (f"?{qs}" if qs else '')


# Reddit listings only page forwards (an `after` cursor), so the pager carries the
# cursors of the pages already visited in a `prev` param: "next" pushes the current
# page's cursor onto it, "previous" pops the last one back off.
_CURSOR_RE = re.compile(r'^[\w\-.=]{1,200}$')
_PREV_MAX = 40


def pager_urls(next_url):
    """(previous-page URL or None, next-page URL or None) for a paginated listing."""
    after = request.args.get('after', '').strip()
    if not _CURSOR_RE.match(after):
        return None, next_url
    trail = [c for c in request.args.get('prev', '').split(',') if _CURSOR_RE.match(c)][-_PREV_MAX:]

    def with_args(url, **params):
        path, _, qs = url.partition('?')
        args = [(k, v) for k, v in parse_qsl(qs) if k not in params]
        args += [(k, v) for k, v in params.items() if v]
        return path + ('?' + urlencode(args) if args else '')

    here = with_args(current_url(), after='', prev='')
    prev_url = with_args(here, after=trail[-1] if trail else '', prev=','.join(trail[:-1]))
    if next_url:
        next_url = with_args(next_url, prev=','.join((trail + [after])[-_PREV_MAX:]))
    return prev_url, next_url


def register(app):
    for f in (timeago, fmtnum, fmtdate, fmtdatetime, usable_bg, md):
        app.add_template_filter(f)
    for fn in (is_bot, link_domain, is_article, article_url):
        app.add_template_global(fn)
    app.add_template_global(download_url)
    app.add_template_global(current_url)
    app.add_template_global(pager_urls)

    def asset_v(filename):
        try:
            return str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
        except OSError:
            return '0'

    @app.context_processor
    def _inject_globals():
        return dict(asset_v=asset_v, proxy_media=app.config['PROXY_MEDIA'], csp_nonce=g.get('csp_nonce', ''),
                    default_settings=DEFAULT_SETTINGS, disable_personalized_home=DISABLE_PERSONALIZED_HOME,
                    ns=all_prefs())
