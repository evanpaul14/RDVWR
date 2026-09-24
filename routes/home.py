"""Home feed: the logged-in shreddit feed when a cookie is supplied, else the anonymous front page."""
import re
import uuid
from urllib.parse import urlparse, parse_qs
from flask import Blueprint, jsonify, request, make_response
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from media_detection import process_post, extract_posts
from reddit_client import reddit_get, PROXIES
from shreddit import _parse_shreddit_post
from helpers import (CACHE_TTL_FEED, DISABLE_PERSONALIZED_HOME, FEED_SORTS, add_time_param, listing_params,
                     json_or_error, hydrate_linked_posts, log, UpstreamError)

bp = Blueprint("home", __name__)

_SHREDDIT_SORTS = {"best": "HOT", "hot": "HOT", "new": "NEW", "top": "TOP",
                   "rising": "RISING", "controversial": "CONTROVERSIAL"}
# Top-level feed elements that are never section labels.
_NON_LABEL_TAGS = {'script', 'hr', 'faceplate-loader', 'faceplate-partial', 'ac-publish',
                   'style', 'link', 'meta', 'shreddit-ad-post'}
_FLAIR_KEYS = ('flair', 'flair_richtext', 'flair_type', 'flair_bg', 'flair_tc')


def fetch_frontpage(sort, t='', after='', timeout=10):
    """Reddit's anonymous (logged-out) front page: {"posts", "after"}."""
    resp = reddit_get(f"https://www.reddit.com/{sort}.json",
                      params=listing_params(after, sort=sort, t=t), timeout=timeout)
    if resp.status_code != 200:
        raise UpstreamError.from_status(resp.status_code)
    listing = resp.json()["data"]
    posts   = extract_posts(listing)
    hydrate_linked_posts(posts)
    return {"posts": posts, "after": listing.get("after")}


def _is_ad(article):
    return bool(article.find('shreddit-ad-post')) or 'promoted' in str(article.attrs).lower()


def _parse_feed_posts(soup):
    """Posts from a shreddit feed page, labelling the first post after each section heading."""
    posts = []
    current_label = None
    last_source = None
    for child in soup.children:
        if not getattr(child, 'name', None):
            continue
        if child.name == 'article':
            post_el = child.find('shreddit-post')
            if not post_el or post_el.has_attr('promoted') or _is_ad(child):
                continue
            post = _parse_shreddit_post(post_el)
            src = post.get('recommendation_source', '')
            if current_label and src != last_source:
                post['feed_label'] = current_label
                current_label = None
            last_source = src
            posts.append(post)
        elif child.name not in _NON_LABEL_TAGS:
            txt = child.get_text(separator=' ', strip=True)
            if txt and len(txt) < 300:
                current_label = txt
    return posts


def _merge_info_data(posts):
    """Fill in flair, galleries and video details missing from shreddit's HTML via info.json."""
    try:
        resp = reddit_get('https://www.reddit.com/api/info.json',
                          params={'id': ','.join(f't3_{p["id"]}' for p in posts), 'raw_json': 1}, timeout=8)
        if not resp.ok:
            return
        by_id = {c['data']['id']: c['data'] for c in resp.json()['data']['children']}
        for post in posts:
            d = by_id.get(post['id'])
            if not d:
                continue
            full = process_post(d)
            for k in _FLAIR_KEYS:
                post[k] = full[k]
            if post['post_hint'] == 'gallery' and not post['gallery'] and full.get('gallery'):
                post['gallery'] = full['gallery']
                if full.get('preview_img'):
                    post['preview_img'] = full['preview_img']
            if post.get('crosspost_from'):
                # info.json knows the real video encoding; shreddit.py has to guess.
                if full.get('crosspost_from'):
                    post['crosspost_from'] = full['crosspost_from']
            elif full.get('is_video') and full.get('hls_url'):
                post['is_video'] = True
                for k in ('video_url', 'hls_url', 'audio_url'):
                    post[k] = full[k]
    except Exception as e:
        log.warning("home-feed flair/gallery batch-fetch failed: %s", e)


def _next_cursor(soup, text):
    partial = soup.find('faceplate-partial', id='feed-next-page-partial')
    if partial and partial.get('src'):
        qs = parse_qs(urlparse(partial['src']).query)
        cursor = (qs.get('after') or qs.get('cursor') or [None])[0]
        if cursor:
            return cursor
    m = re.search(r'"after"\s*:\s*"([A-Za-z0-9_-]+)"', text)
    return m.group(1) if m else None


def _fetch_personal_feed(cookie, sort, t, after, distance):
    """The visitor's logged-in home feed as {"posts", "after"}, or None on failure."""
    params = {"sort": _SHREDDIT_SORTS.get(sort, "HOT"), "distance": distance, "adDistance": 2,
              "navigationSessionId": str(uuid.uuid4()), "referer": "www.reddit.com"}
    add_time_param(params, sort, t)
    if after:
        params["after"] = params["cursor"] = after
    try:
        resp = cffi_requests.get(
            "https://www.reddit.com/svc/shreddit/feeds/home-feed",
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:151.0) Gecko/20100101 Firefox/151.0",
                "Accept": "text/vnd.reddit.partial+html, text/html;q=0.9",
                "Cookie": cookie,
                "x-reddit-client-version": "2026-06-04T20:11Z~59b9f87c",
                "Referer": "https://www.reddit.com/?feed=home",
                "x-original-referer": "https://www.reddit.com/?feed=home",
            },
            impersonate="firefox133",
            proxies=PROXIES,
            timeout=15,
            allow_redirects=False,  # never forward the visitor's cookie elsewhere
        )
        if resp.status_code != 200:
            log.warning("shreddit home-feed non-OK: %s %.300s", resp.status_code, resp.text)
            return None
        soup = BeautifulSoup(resp.text, 'html.parser')
        posts = _parse_feed_posts(soup)
        if posts:
            _merge_info_data(posts)
        hydrate_linked_posts(posts)
        return {"posts": posts, "after": _next_cursor(soup, resp.text)}
    except Exception as e:
        log.warning("shreddit home-feed failed: %s", e)
        return None


@bp.route("/api/home")
def get_home():
    sort  = request.args.get("sort", "best")
    t     = request.args.get("t", "")
    after = request.args.get("after", "")
    if sort not in FEED_SORTS:
        sort = "best"
    try:
        distance = min(max(int(request.args.get("distance", 4)), 4), 500)
    except ValueError:
        distance = 4

    cookie = "" if DISABLE_PERSONALIZED_HOME else request.headers.get("X-Reddit-Cookie", "").strip()
    if cookie:
        feed = _fetch_personal_feed(cookie, sort, t, after, distance)
        if feed is not None:
            resp = make_response(jsonify({**feed, "via": "shreddit"}))
            resp.headers['Cache-Control'] = 'private, no-store'
            return resp

    return json_or_error(lambda: {**fetch_frontpage(sort, t, after), "via": "anonymous"}, CACHE_TTL_FEED)
