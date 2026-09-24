"""Sanitizing Reddit's pre-rendered *_html fields (selftext_html, body_html,
description_html, wiki content_html) for the server-rendered no-JS fallback, which
can't pull in a client-side sanitizer like DOMPurify since that needs JS to run.

Strips every tag and attribute except a small safe set — no script/style/event-handler/
class/id attrs can survive — and otherwise renders the way the JS markdown renderer
(static/render.js renderMd) would: reddit.com links stay on this site, >!spoilers!< stay
hidden, and bare image links show the image."""
import re
import html as html_lib
from html.parser import HTMLParser
from urllib.parse import quote as url_quote, urlparse, parse_qs, urlencode

_SANITIZE_ALLOWED_TAGS = {
    'p', 'br', 'a', 'ul', 'ol', 'li', 'strong', 'em', 'b', 'i', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'code', 'pre', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'hr', 'del', 'sup', 'sub',
}


# Links to subreddits, users and live threads — on reddit.com or root-relative — are
# rewritten to the same path on this site (opened in-place, like the JS app's link
# interception in app.js). Other root-relative links (e.g. /message/compose) only
# exist on reddit.com, so they're pointed there.
_LOCAL_PATH = r'/(?:r|u|user|live)/[^?#]*'
_REDDIT_LINK_RE = re.compile(rf'^(?:https?://(?:www\.|old\.|new\.|np\.)?reddit\.com)?({_LOCAL_PATH})(?:\?([^#]*))?', re.I)
# A subreddit search link (e.g. a sidebar's "filter by flair" links).
_SUB_SEARCH_RE = re.compile(r'^/r/([A-Za-z0-9_]+)/search/?$', re.I)
# Bare image links in comments/posts (e.g. a pasted preview.redd.it URL) render inline,
# same as the JS markdown renderer: Reddit's own image hosts via the /api/img proxy.
_INLINE_IMG_HOSTS = frozenset({'preview.redd.it', 'external-preview.redd.it'})
_INLINE_IMG_EXT_RE = re.compile(r'\.(?:jpe?g|gif|png|webp|avif)(?:\?|$)', re.I)
# Reddit renders a comment's giphy embed as a link to the giphy page.
_GIPHY_PAGE_RE = re.compile(r'^https://giphy\.com/gifs/(?:[\w-]*-)?([A-Za-z0-9]+)/?$')
# Marks third-party inline media, which templates swap for a plain link when the
# visitor has "don't embed third-party media" on (template_helpers.md).
EXTERNAL_MEDIA_CLASS = 'md-ext-media'


def local_reddit_path(href):
    """This site's path for a subreddit/user/live-thread link, else None."""
    m = _REDDIT_LINK_RE.match(href)
    if not m:
        return None
    path, query = m.group(1), parse_qs(m.group(2) or '')
    search = _SUB_SEARCH_RE.match(path)
    if search and query.get('q'):
        restrict = query.get('restrict_sr', [''])[0] in ('on', '1', 'true')
        return '/search?' + urlencode({'q': query['q'][0], **({'sub': search.group(1)} if restrict else {})})
    return path


def _inline_img_src(href):
    """(src, is_third_party) for a link that should render as an inline image, else None."""
    parsed = urlparse(href)
    if parsed.hostname in _INLINE_IMG_HOSTS and _INLINE_IMG_EXT_RE.search(parsed.path):
        return '/api/img?url=' + url_quote(href, safe=''), False
    m = _GIPHY_PAGE_RE.match(href)
    if m:
        return f'https://media.giphy.com/media/{m.group(1)}/giphy.gif', True
    return None


class _AllowlistHtmlSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._span_stack = []   # whether each open <span> was emitted
        self._img_link = None   # (href, (src, is_third_party), [link text]) inside an inline-image <a>
        self._skip_depth = 0    # >0 inside <script>/<style>, whose text is dropped

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self._skip_depth += 1
            return
        if self._img_link:
            return
        if tag == 'span':
            # Reddit's >!spoiler!< markup; revealed on hover/focus by CSS (.ns-root .spoiler).
            is_spoiler = 'md-spoiler-text' in (attrs.get('class') or '').split()
            self._span_stack.append(is_spoiler)
            if is_spoiler:
                self.out.append('<span class="spoiler" tabindex="0">')
            return
        if tag not in _SANITIZE_ALLOWED_TAGS:
            return
        if tag == 'a':
            href = attrs.get('href') or ''
            src = _inline_img_src(href) if href.startswith('https://') else None
            local = local_reddit_path(href)
            if src:
                self._img_link = (href, src, [])
            elif local:
                self.out.append(f'<a href="{html_lib.escape(local, quote=True)}">')
            elif href.startswith(('http://', 'https://', '/')) and not href.startswith('//'):
                if href.startswith('/'):
                    href = 'https://www.reddit.com' + href
                self.out.append(f'<a href="{html_lib.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
            else:
                self.out.append('<a>')
        else:
            self.out.append(f'<{tag}>')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._img_link:
            if tag == 'a':
                self._flush_img_link()
            return
        if tag == 'span':
            if self._span_stack and self._span_stack.pop():
                self.out.append('</span>')
            return
        if tag in _SANITIZE_ALLOWED_TAGS:
            self.out.append(f'</{tag}>')

    def handle_startendtag(self, tag, attrs):
        if tag in ('br', 'hr') and not self._img_link:
            self.out.append(f'<{tag}>')

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._img_link:
            self._img_link[2].append(data)
        else:
            self.out.append(html_lib.escape(data))

    def _flush_img_link(self):
        href, (src, third_party), text = self._img_link
        self._img_link = None
        caption = ''.join(text).strip()
        link = href if third_party else src
        cls = f' class="{EXTERNAL_MEDIA_CLASS}"' if third_party else ''
        img = (f'<a{cls} href="{html_lib.escape(link, quote=True)}" target="_blank" rel="noopener">'
               f'<img src="{html_lib.escape(src, quote=True)}" alt="" loading="lazy"></a>')
        if caption and caption != href:
            img = f'<span class="md-img-block">{img}<span class="md-img-caption">{html_lib.escape(caption)}</span></span>'
        self.out.append(img)

    def get_html(self):
        if self._img_link:
            self._flush_img_link()
        return ''.join(self.out)


def sanitize_reddit_html(raw_html):
    """Sanitize one of Reddit's pre-rendered *_html fields down to a small safe tag
    allowlist. `raw_html` is expected already HTML-unescaped and SC_OFF/SC_ON-stripped."""
    if not raw_html:
        return ''
    parser = _AllowlistHtmlSanitizer()
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        return ''
    return parser.get_html()


_SC_MARKER_RE = re.compile(r'<!--\s*SC_(?:OFF|ON)\s*-->')


def clean_reddit_html(raw_html):
    """Unescape + strip Reddit's markdown-region markers + sanitize one of its
    pre-rendered *_html fields, ready to render with `| safe`."""
    if not raw_html:
        return ''
    unescaped = _SC_MARKER_RE.sub('', html_lib.unescape(raw_html)).strip()
    return sanitize_reddit_html(unescaped)
