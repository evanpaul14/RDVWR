"""Post ids of a combined feed (r/a+b) from the HTML listing page reddit.com serves Safari
visitors. Reddit's JSON API refuses the New and Rising sorts of combined feeds, but this page
still has them (found via Nachtalb/redlib). Only the ids are read; the posts come from by_id."""
import random
import re
import time
from curl_cffi import requests as cffi_requests
from core.helpers import UpstreamError, log
from reddit import client
from reddit.budget import request as budget_request

_THING_RE = re.compile(r'<div\b([^>]*\bdata-fullname="t3_([a-z0-9]+)"[^>]*)>')
_NEXT_RE  = re.compile(r'class="next-button"><a href="[^"]*[?&;]after=t3_([a-z0-9]+)')
_PAGE_SIZE = 25
# Only desktop Safari's TLS fingerprints get this page: Chrome's are answered with a block
# page and the iOS ones are redirected to the home page.
_SAFARI_TRIES = 3


def parse_listing(page):
    """(post ids, id to continue after or ""): the posts on the page in order, ads left out."""
    ids = []
    for attrs, post_id in _THING_RE.findall(page):
        if "promoted" in attrs.split('data-', 1)[0] or post_id in ids:
            continue
        ids.append(post_id)
    nxt = _NEXT_RE.search(page)
    return ids, nxt.group(1) if nxt else ""


def fetch_listing_ids(subreddit, sort, after=""):
    """(ids, next_after) for `r/<subreddit>/<sort>` (subreddit may be a+b). Raises
    UpstreamError when Reddit won't serve the page to any Safari fingerprint."""
    url = f"https://www.reddit.com/r/{subreddit}/{sort}/"
    params = {"count": _PAGE_SIZE, "after": f"t3_{after}"} if after else {}
    now = time.time()
    profiles = [p for p in client._CFFI_PROFILES
                if p.startswith("safari") and not p.endswith("_ios") and client._blocked_profiles.get(p, 0) <= now]
    random.shuffle(profiles)
    for _ in range(_SAFARI_TRIES):
        if not profiles:
            break
        profile = profiles[0]
        resp = budget_request(cffi_requests.get, url, impersonate=profile, proxies=client.PROXIES,
                              params=params, timeout=15, headers={"Accept": "text/html"})
        if client.is_fingerprint_block(resp):
            client.fingerprint_blocked(profile)
            profiles.remove(profile)
            continue
        if resp.status_code != 200:
            break
        ids, nxt = parse_listing(resp.text)
        if ids:
            return ids, nxt
        log.warning("scraped listing had no posts sub=%s sort=%s", subreddit, sort)
        break
    raise UpstreamError("Reddit returned 403", 403)
