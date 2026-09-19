"""Home feed: the logged-in shreddit feed when a cookie is supplied, else the anonymous front page."""
import re
import uuid
import requests
from urllib.parse import urlparse, parse_qs
from flask import Blueprint, jsonify, request, make_response
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from media_detection import process_post, extract_posts
from reddit_client import reddit_get, PROXIES
from shreddit import _parse_shreddit_post
from helpers import CACHE_TTL_FEED, DISABLE_PERSONALIZED_HOME, FEED_LIMIT, add_time_param, cached_json, error_response, hydrate_linked_posts, log

bp = Blueprint("home", __name__)


@bp.route("/api/home")
def get_home():
    sort  = request.args.get("sort", "best")
    t     = request.args.get("t", "")
    after = request.args.get("after", "")
    if sort not in {"best", "hot", "new", "top", "rising", "controversial"}:
        sort = "best"
    try:
        distance = min(max(int(request.args.get("distance", 4)), 4), 500)
    except ValueError:
        distance = 4

    cookie = "" if DISABLE_PERSONALIZED_HOME else request.headers.get("X-Reddit-Cookie", "").strip()
    if cookie:
        shreddit_sort = {"best": "HOT", "hot": "HOT", "new": "NEW",
                         "top": "TOP", "rising": "RISING",
                         "controversial": "CONTROVERSIAL"}.get(sort, "HOT")
        nav_id = str(uuid.uuid4())
        params = {"sort": shreddit_sort, "distance": distance, "adDistance": 2,
                  "navigationSessionId": nav_id, "referer": "www.reddit.com"}
        add_time_param(params, sort, t)
        if after:
            params["after"] = after
            params["cursor"] = after
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
                # A redirect would carry the manually-set Cookie header to wherever
                # Location points, even cross-host — the endpoint is fixed and not
                # attacker-steerable, but there's no reason to allow it either.
                allow_redirects=False,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                posts = []
                current_label = None
                last_source = None
                _SKIP_TAGS = {'script', 'hr', 'faceplate-loader', 'faceplate-partial',
                              'ac-publish', 'style', 'link', 'meta',
                              'shreddit-ad-post'}
                for child in soup.children:
                    if not hasattr(child, 'name') or not child.name:
                        continue
                    if child.name == 'article':
                        post_el = child.find('shreddit-post')
                        if post_el:
                            if (post_el.has_attr('promoted') or
                                    child.find('shreddit-ad-post') or
                                    'promoted' in str(child.attrs).lower()):
                                continue
                            post = _parse_shreddit_post(post_el)
                            src = post.get('recommendation_source', '')
                            if current_label and src != last_source:
                                post['feed_label'] = current_label
                                current_label = None
                            last_source = src
                            posts.append(post)
                        elif child.find('shreddit-ad-post') or 'promotedlink' in ' '.join(child.get('class') or []):
                            continue
                    elif child.name not in _SKIP_TAGS:
                        txt = child.get_text(separator=' ', strip=True)
                        if txt and len(txt) < 300:
                            current_label = txt
                # Batch-fetch gallery data + flair (not present in shreddit's HTML) via the JSON API
                if posts:
                    ids_str = ','.join(f't3_{p["id"]}' for p in posts)
                    try:
                        gi = reddit_get('https://www.reddit.com/api/info.json',
                                        params={'id': ids_str, 'raw_json': 1}, timeout=8)
                        if gi.ok:
                            by_id = {c['data']['id']: c['data']
                                     for c in gi.json()['data']['children']}
                            for post in posts:
                                d = by_id.get(post['id'])
                                if not d:
                                    continue
                                full = process_post(d)
                                post['flair'] = full['flair']
                                post['flair_richtext'] = full['flair_richtext']
                                post['flair_type'] = full['flair_type']
                                post['flair_bg'] = full['flair_bg']
                                post['flair_tc'] = full['flair_tc']
                                if post['post_hint'] == 'gallery' and not post['gallery'] and full.get('gallery'):
                                    post['gallery'] = full['gallery']
                                    if full.get('preview_img'):
                                        post['preview_img'] = full['preview_img']
                                if full.get('is_video') and full.get('hls_url'):
                                    post['is_video'] = True
                                    post['video_url'] = full['video_url']
                                    post['hls_url'] = full['hls_url']
                                    post['audio_url'] = full['audio_url']
                    except Exception as ge:
                        log.warning("home-feed flair/gallery batch-fetch failed: %s", ge)
                hydrate_linked_posts(posts)
                next_after = None
                next_partial = soup.find('faceplate-partial', id='feed-next-page-partial')
                if next_partial and next_partial.get('src'):
                    qs = parse_qs(urlparse(next_partial['src']).query)
                    next_after = (qs.get('after') or qs.get('cursor') or [None])[0]
                if not next_after:
                    m = re.search(r'"after"\s*:\s*"([A-Za-z0-9_-]+)"', resp.text)
                    next_after = m.group(1) if m else None
                resp_out = make_response(jsonify({"posts": posts, "after": next_after, "via": "shreddit"}))
                resp_out.headers['Cache-Control'] = 'private, no-store'
                return resp_out
            else:
                log.warning("shreddit home-feed non-OK: %s %.300s", resp.status_code, resp.text)
        except Exception as e:
            log.warning("shreddit home-feed failed: %s", e)

    # Fallback: anonymous JSON API
    url    = f"https://www.reddit.com/{sort}.json"
    params = {"limit": FEED_LIMIT, "raw_json": 1}
    add_time_param(params, sort, t)
    if after:
        params["after"] = after
    try:
        resp = reddit_get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({"error": f"Reddit returned {resp.status_code}"}), resp.status_code
        listing = resp.json()["data"]
        posts   = extract_posts(listing)
        hydrate_linked_posts(posts)
        return cached_json({"posts": posts, "after": listing.get("after"), "via": "anonymous"}, CACHE_TTL_FEED)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception:
        return error_response(500)
