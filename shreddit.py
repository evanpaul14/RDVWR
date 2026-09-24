"""Parsers for shreddit (new-Reddit web component) HTML post markup."""
import re
from datetime import datetime
from urllib.parse import urlparse
from media_detection import (extract_redgifs_id, YOUTUBE_RE, STREAMABLE_RE, VREDDDIT_RE, REDDIT_IMAGE_HOSTS,
                              gif_from_url, parse_linked_post, proxy_if_reddit_preview, build_reddit_video_urls)

_SUB_HREF_RE      = re.compile(r'^/r/([^/]+)/?$')
_COMMENTS_HREF_RE = re.compile(r'^/r/([^/]+)/comments/([A-Za-z0-9]+)')


def _int_attr(el, name, default=0):
    try:
        return int(el.get(name, default) or default)
    except (TypeError, ValueError):
        return default


def _img_src(img):
    return img.get('src', '') or img.get('data-lazy-src', '') or img.get('data-src', '') or ''


def _is_reddit_image(src):
    return (urlparse(src).hostname or '') in REDDIT_IMAGE_HOSTS


def _parse_shreddit_crosspost(el):
    """Original post's subreddit/title/media from a crosspost's 'post-media-container'."""
    container = el.find('div', {'slot': 'post-media-container'})
    if not container:
        return None

    orig_sub = ''
    orig_id = ''
    orig_title = ''
    credit_bar = container.find(class_='crosspost-credit-bar')
    if credit_bar:
        a = credit_bar.find('a', href=True)
        if a:
            m = _SUB_HREF_RE.match(a['href'])
            if m:
                orig_sub = m.group(1)
    title_div = container.find(class_='crosspost-title')
    if title_div:
        a = title_div.find('a', href=True)
        if a:
            orig_title = a.get_text(strip=True)
            pm = _COMMENTS_HREF_RE.match(a['href'])
            if pm:
                orig_sub = orig_sub or pm.group(1)
                orig_id = pm.group(2)

    # Link-post crossposts use a plain card without the classes above.
    if not orig_id:
        comments_a = container.find('a', href=_COMMENTS_HREF_RE)
        if comments_a:
            pm = _COMMENTS_HREF_RE.match(comments_a['href'])
            orig_sub = orig_sub or pm.group(1)
            orig_id = pm.group(2)
    if not orig_sub:
        sub_a = container.find('a', href=_SUB_HREF_RE)
        if sub_a:
            orig_sub = _SUB_HREF_RE.match(sub_a['href']).group(1)
    if not orig_title:
        # Both crosspost-title layouts mark the title element with dir="auto".
        text_div = container.find(attrs={'dir': 'auto'})
        if text_div:
            orig_title = text_div.get_text(strip=True)

    preview_img = None
    gallery = []
    is_video = False
    video_url = hls_url = audio_url = None

    player = container.find('shreddit-player')
    if player:
        src = player.get('src', '') or ''
        m = VREDDDIT_RE.match(src)
        base = m.group(1) if m else None
        if base:
            is_video = True
            # src is the HLS playlist, which doesn't reveal the encoding; assume DASH.
            urls = build_reddit_video_urls(base, cmaf=False)
            hls_url, video_url, audio_url = urls['hls_url'], urls['video_url'], urls['audio_url']
        poster = player.get('poster', '') or ''
        if poster:
            preview_img = proxy_if_reddit_preview(poster)
    else:
        seen = set()
        for img in container.find_all('img'):
            src = _img_src(img)
            if not src or src in seen or not _is_reddit_image(src):
                continue
            seen.add(src)
            proxied = proxy_if_reddit_preview(src)
            if img.has_attr('data-post-media-primary') or not gallery:
                gallery.append({'url': proxied, 'width': 0, 'height': 0, 'caption': ''})
        if len(gallery) == 1:
            preview_img = gallery[0]['url']
            gallery = []
        elif gallery:
            preview_img = gallery[0]['url']

    if not orig_id and not preview_img and not is_video and not gallery:
        return None

    return {
        'id': orig_id, 'title': orig_title, 'author': '[deleted]', 'subreddit': orig_sub,
        'score': 0, 'upvote_ratio': 0, 'num_comments': 0, 'created_utc': 0,
        'url': f'https://www.reddit.com/r/{orig_sub}/comments/{orig_id}' if orig_id else '',
        'permalink': f'https://www.reddit.com/r/{orig_sub}/comments/{orig_id}' if orig_id else '',
        'is_self': False, 'selftext': '', 'selftext_html': None,
        'preview_img': preview_img, 'gallery': gallery,
        'is_video': is_video, 'video_url': video_url, 'hls_url': hls_url, 'audio_url': audio_url,
        'youtube_id': None, 'tiktok_id': None, 'streamable_id': None, 'embed_url': None,
        'redgifs_id': None, 'gif_url': None, 'gif_is_video': False, 'imgur_album_id': None,
        'post_hint': '', 'is_devvit': False, 'devvit_url': None, 'over_18': el.has_attr('is-nsfw'),
        'flair': '', 'flair_richtext': [], 'flair_type': 'text', 'flair_bg': '', 'flair_tc': 'dark',
        'domain': el.get('domain', ''), 'poll': None, 'crosspost_from': None, 'linked_post': None,
        'is_stickied': False, 'is_oc': False, 'is_spoiler': False, 'locked': False,
        'edited_utc': None, 'awards': [],
    }


def _parse_shreddit_post(el):
    raw_id  = el.get('id', '')
    post_id = raw_id[3:] if raw_id.startswith('t3_') else raw_id
    permalink = el.get('permalink', '')
    content_href = el.get('content-href', '') or ''
    post_type = el.get('post-type', '')

    try:
        created_utc = int(datetime.fromisoformat(
            el.get('created-timestamp', '').replace('+0000', '+00:00')
        ).timestamp())
    except Exception:
        created_utc = 0

    try:
        upvote_ratio = round(float(el.get('upvote-ratio', 0)) * 100)
    except (TypeError, ValueError):
        upvote_ratio = 0

    domain_str = el.get('domain', '')
    is_self = post_type in ('self', 'text', 'poll') or domain_str.startswith('self.')
    is_crosspost = post_type == 'crosspost' or el.has_attr('is-crosspost')
    url = content_href if (content_href and not is_self and not is_crosspost) else f'https://www.reddit.com{permalink}'

    selftext = ''
    selftext_html = None
    if is_self:
        body_el = el.find(slot='text-body') or el.find('div', {'slot': 'text-body'})
        if not body_el:
            body_el = el.find('faceplate-html', {'slot': 'text-body'})
        if body_el:
            for a in body_el.find_all('a'):
                a.unwrap()
            inner = body_el.decode_contents().strip()
            if inner:
                selftext_html = inner
                selftext = body_el.get_text(separator=' ').strip()

    preview_img = None
    if post_type == 'image' and content_href:
        preview_img = proxy_if_reddit_preview(content_href)

    if not preview_img and post_type == 'link':
        thumb_div = el.find('div', {'slot': 'thumbnail'})
        thumb_img = thumb_div.find('img') if thumb_div else None
        thumb_src = thumb_img.get('src', '') if thumb_img else ''
        if thumb_src:
            preview_img = proxy_if_reddit_preview(thumb_src)

    is_gallery_url = content_href and '/gallery/' in content_href
    gallery = []
    if post_type == 'gallery' or is_gallery_url:
        # Each slide <li> holds several <img> variants; take one, preferring full-res.
        for li in el.find_all('li', slot=re.compile(r'^page-\d+$')):
            lightboxed = li.find(class_='lightboxed-content')
            img = lightboxed.find('img') if lightboxed else None
            if not img:
                fig = li.find('figure')
                img = (fig.find('img') if fig else None) or li.find('img')
            if not img:
                continue
            src = _img_src(img)
            if not src or not _is_reddit_image(src):
                continue
            fig = img.find_parent('figure')
            cap_el = fig.find('figcaption') if fig else None
            gallery.append({'url': proxy_if_reddit_preview(src),
                            'width': _int_attr(img, 'width'), 'height': _int_attr(img, 'height'),
                            'caption': cap_el.get_text().strip() if cap_el else ''})
        if gallery and not preview_img:
            preview_img = gallery[0]['url']

    is_video = post_type in ('video', 'gif') and bool(content_href) and 'v.redd.it' in content_href
    video_url = hls_url = audio_url = None
    if is_video:
        m = VREDDDIT_RE.match(content_href)
        base = m.group(1) if m else None
        if base:
            urls = build_reddit_video_urls(base, cmaf='/DASH_' not in content_href)
            video_url = content_href if '/DASH_' in content_href else urls['video_url']
            hls_url, audio_url = urls['hls_url'], urls['audio_url']
        else:
            is_video = False

    redgifs_id = extract_redgifs_id(url)
    yt = YOUTUBE_RE.search(url); youtube_id = yt.group(1) if yt else None
    sm = STREAMABLE_RE.search(url); streamable_id = sm.group(1) if sm else None

    gif_url, gif_is_video = None, False
    if not is_video and not redgifs_id and not youtube_id and not streamable_id:
        gif_url, gif_is_video = gif_from_url(url)
        if not gif_url and post_type == 'gif' and content_href:
            gif_url, gif_is_video = gif_from_url(content_href)
            if not gif_url:
                gif_url, gif_is_video = content_href, True

    is_devvit = post_type == 'custom'
    devvit_url = (f'https://sh.reddit.com/r/{el.get("subreddit-name", "")}/comments/{post_id}'
                  if is_devvit else None)

    linked_post = parse_linked_post(url) if not is_self and not is_crosspost else None

    icon = el.get('award-icon-url', '')
    awards = [{'name': '', 'count': _int_attr(el, 'award-count', 1), 'icon': icon}] if icon else []

    crosspost_from = _parse_shreddit_crosspost(el) if is_crosspost else None

    return {
        'id': post_id, 'title': el.get('post-title', ''),
        'author': el.get('author', '[deleted]'),
        'subreddit': el.get('subreddit-name', ''),
        'score': _int_attr(el, 'score'), 'upvote_ratio': upvote_ratio,
        'num_comments': _int_attr(el, 'comment-count'), 'created_utc': created_utc,
        'url': url, 'permalink': f'https://www.reddit.com{permalink}',
        'is_self': is_self, 'selftext': selftext, 'selftext_html': selftext_html,
        'preview_img': preview_img, 'gallery': gallery,
        'is_video': is_video, 'video_url': video_url,
        'hls_url': hls_url, 'audio_url': audio_url,
        'youtube_id': youtube_id, 'tiktok_id': None,
        'streamable_id': streamable_id, 'embed_url': None,
        'redgifs_id': redgifs_id, 'gif_url': gif_url, 'gif_is_video': gif_is_video,
        'imgur_album_id': None, 'post_hint': post_type,
        'is_devvit': is_devvit, 'devvit_url': devvit_url,
        'over_18': el.has_attr('is-nsfw'),
        'flair': '', 'flair_richtext': [], 'flair_type': 'text',
        'flair_bg': '', 'flair_tc': 'dark',
        'domain': domain_str, 'poll': None,
        'crosspost_from': crosspost_from, 'linked_post': linked_post, 'is_stickied': False,
        'is_oc': False, 'is_spoiler': el.has_attr('is-spoiler') or el.has_attr('spoiler'), 'locked': False,
        'edited_utc': None, 'awards': awards,
        'recommendation_source': el.get('recommendation-source', ''),
        'feed_label': None,
    }
