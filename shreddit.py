"""Parsers for shreddit (new-Reddit web component) HTML post markup."""
import re
from datetime import datetime
from urllib.parse import urlparse
from media_detection import (extract_redgifs_id, YOUTUBE_RE, STREAMABLE_RE, VREDDDIT_RE, LINK_POST_RE,
                              GIFV_RE, proxy_if_reddit_preview, build_reddit_video_urls)


def _parse_shreddit_crosspost(el):
    """Extract the original post's subreddit/title/media from a crosspost shreddit-post
    element's embedded 'post-media-container' block (the original media is inlined there)."""
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
            m = re.match(r'^/r/([^/]+)/?$', a['href'])
            if m:
                orig_sub = m.group(1)
    title_div = container.find(class_='crosspost-title')
    if title_div:
        a = title_div.find('a', href=True)
        if a:
            orig_title = a.get_text(strip=True)
            pm = re.match(r'^/r/([^/]+)/comments/([A-Za-z0-9]+)', a['href'])
            if pm:
                orig_sub = orig_sub or pm.group(1)
                orig_id = pm.group(2)

    # Link-post crossposts render a plain card instead of the
    # crosspost-credit-bar/crosspost-title elements above (no wrapping <a> on
    # the title, and the sub/comments links live in a differently-classed
    # block) — fall back to structure-agnostic lookups within the container.
    if not orig_id:
        comments_a = container.find('a', href=re.compile(r'^/r/[^/]+/comments/[A-Za-z0-9]+'))
        if comments_a:
            pm = re.match(r'^/r/([^/]+)/comments/([A-Za-z0-9]+)', comments_a['href'])
            if pm:
                orig_sub = orig_sub or pm.group(1)
                orig_id = pm.group(2)
    if not orig_sub:
        sub_a = container.find('a', href=re.compile(r'^/r/[^/]+/?$'))
        if sub_a:
            m = re.match(r'^/r/([^/]+)/?$', sub_a['href'])
            if m:
                orig_sub = m.group(1)
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
            # src is always the HLS playlist URL here, which carries no DASH-vs-CMAF hint;
            # there's no way to detect the actual encoding from a shreddit-player tag alone.
            urls = build_reddit_video_urls(base, cmaf=False)
            hls_url, video_url, audio_url = urls['hls_url'], urls['video_url'], urls['audio_url']
        poster = player.get('poster', '') or ''
        if poster:
            preview_img = proxy_if_reddit_preview(poster)
    else:
        seen = set()
        for img in container.find_all('img'):
            src = img.get('src', '') or img.get('data-lazy-src', '') or img.get('data-src', '') or ''
            if not src or src in seen:
                continue
            h = urlparse(src).hostname or ''
            if h not in ('preview.redd.it', 'external-preview.redd.it', 'i.redd.it'):
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

    try: score = int(el.get('score', 0))
    except Exception: score = 0
    try: upvote_ratio = round(float(el.get('upvote-ratio', 0)) * 100)
    except Exception: upvote_ratio = 0
    try: num_comments = int(el.get('comment-count', 0))
    except Exception: num_comments = 0

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
        # Each slide is a <li slot="page-N"> containing three <img>s: a blurred
        # background decoration, a low-res visible preview, and a full-res copy
        # hidden in .lightboxed-content for the zoom viewer — all three carry
        # different exact src URLs, so collecting every <img> under the post
        # doubles (or triples) each slide. Pick one image per slot instead,
        # preferring the hidden full-res copy.
        for li in el.find_all('li', slot=re.compile(r'^page-\d+$')):
            lightboxed = li.find(class_='lightboxed-content')
            img = lightboxed.find('img') if lightboxed else None
            if not img:
                fig = li.find('figure')
                img = (fig.find('img') if fig else None) or li.find('img')
            if not img:
                continue
            src = (img.get('src', '') or img.get('data-lazy-src', '') or
                   img.get('data-src', '') or '')
            if not src:
                continue
            h = urlparse(src).hostname or ''
            if h not in ('preview.redd.it', 'external-preview.redd.it', 'i.redd.it'):
                continue
            proxied = proxy_if_reddit_preview(src)
            fig = img.find_parent('figure')
            cap_el = fig.find('figcaption') if fig else None
            try: w = int(img.get('width', 0) or 0)
            except Exception: w = 0
            try: h_val = int(img.get('height', 0) or 0)
            except Exception: h_val = 0
            gallery.append({'url': proxied, 'width': w, 'height': h_val,
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

    gif_url = None
    gif_is_video = False
    if not is_video and not redgifs_id and not youtube_id and not streamable_id:
        lower_url = url.lower().split('?')[0]
        if lower_url.endswith('.gif'):
            gif_url = url
        elif lower_url.endswith('.gifv'):
            gif_url = GIFV_RE.sub('.mp4', url)
            gif_is_video = True
        elif post_type == 'gif' and content_href:
            lower_href = content_href.lower().split('?')[0]
            if lower_href.endswith('.gif'):
                gif_url = content_href
            elif lower_href.endswith('.gifv'):
                gif_url = GIFV_RE.sub('.mp4', content_href)
                gif_is_video = True
            else:
                gif_url = content_href
                gif_is_video = True

    is_devvit = post_type == 'custom'
    devvit_url = (f'https://sh.reddit.com/r/{el.get("subreddit-name", "")}/comments/{post_id}'
                  if is_devvit else None)

    linked_post = None
    if not is_self and not is_crosspost:
        lm = LINK_POST_RE.match(url)
        if lm:
            linked_post = {
                'subreddit': lm.group(1),
                'id':        lm.group(2),
                'title':     lm.group(3).replace('_', ' ').strip() if lm.group(3) else '',
            }

    awards = []
    icon = el.get('award-icon-url', '')
    if icon:
        try: cnt = int(el.get('award-count', 1))
        except Exception: cnt = 1
        awards = [{'name': '', 'count': cnt, 'icon': icon}]

    crosspost_from = _parse_shreddit_crosspost(el) if is_crosspost else None

    return {
        'id': post_id, 'title': el.get('post-title', ''),
        'author': el.get('author', '[deleted]'),
        'subreddit': el.get('subreddit-name', ''),
        'score': score, 'upvote_ratio': upvote_ratio,
        'num_comments': num_comments, 'created_utc': created_utc,
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
