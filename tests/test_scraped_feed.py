"""Combined feeds' New and Rising: post ids scraped from the listing page when the JSON API refuses."""
from unittest.mock import patch

from reddit import scrape
from routes import subreddit as subreddit_routes

PAGE = (
    '<div class=" thing id-t3_aaa odd link " id="thing_t3_aaa" data-fullname="t3_aaa" data-type="link"></div>'
    '<div class=" thing id-t3_ad promoted link " data-fullname="t3_ad1" data-promoted="true"></div>'
    '<div class=" thing id-t3_bbb even link " data-fullname="t3_bbb" data-type="link"></div>'
    '<div data-fullname="t3_aaa"></div>'
    '<span class="next-button"><a href="https://www.reddit.com/r/a+b/new/?count=25&amp;after=t3_bbb" rel="nofollow next">next</a></span>')


def test_parse_listing_skips_ads_and_repeats_and_finds_next():
    assert scrape.parse_listing(PAGE) == (["aaa", "bbb"], "bbb")
    assert scrape.parse_listing("<html>nothing</html>") == ([], "")


def test_parse_listing_last_page_has_no_next():
    assert scrape.parse_listing(PAGE.split('<span class="next-button">')[0])[1] == ""


def test_scraped_combined_feed_builds_posts_in_listing_order(monkeypatch):
    raw = lambda i: {"id": i, "name": f"t3_{i}", "title": i, "author": "x", "subreddit": "a",
                     "permalink": f"/r/a/comments/{i}/", "created_utc": 1.0, "is_self": True, "selftext": ""}
    monkeypatch.setattr(subreddit_routes, "fetch_listing_ids", lambda *a: (["bbb", "aaa"], "aaa"))
    monkeypatch.setattr(subreddit_routes, "fetch_posts_by_id", lambda ids: {i: raw(i) for i in ids})
    out = subreddit_routes._scraped_combined_feed("a+b", "new", "")
    assert [p["id"] for p in out["posts"]] == ["bbb", "aaa"]
    assert out["after"] == "t3_aaa"
