"""Wiki pages from the app's GraphQL API: richtext rendering, heading ids, table of contents."""
import json
from unittest.mock import patch

import pytest

from core.helpers import UpstreamError
from gql import wiki as gql_wiki
from routes import subreddit as subreddit_routes


def _node(status="VALID", content=None, typename="Subreddit"):
    return {"data": {"subredditInfoByName": {
        "__typename": typename, "wiki": {"page": {"status": status, "content": content}}}}}


def _richtext(*blocks):
    return json.dumps({"document": list(blocks)})


def test_wiki_page_renders_richtext_with_toc():
    doc = _richtext(
        {"e": "h", "l": 1, "c": [{"e": "text", "t": "Intro: A/B"}]},
        {"e": "par", "c": [{"e": "text", "t": "hi"}]},
        {"e": "h", "l": 2, "c": [{"e": "text", "t": "Sub"}]})
    with patch.object(gql_wiki, "gql_query", lambda *a, **k: _node(content={"richtext": doc})):
        out = gql_wiki.fetch_gql_wiki("x", "index")
    html = out["content_html"]
    assert out["revision_date"] is None
    assert '<h1 id="wiki_intro.3A_a.2Fb">Intro: A/B</h1>' in html
    assert '<h2 id="wiki_sub">Sub</h2>' in html
    assert '<div class="toc"><ul><li class="wiki_intro.3A_a.2Fb">' in html
    assert '<li class="toc_child"><ul><li class="wiki_sub">' in html
    assert html.startswith('<div class="md wiki">') and "<p>hi</p>" in html


def test_toc_nesting_duplicates_and_unicode():
    html = "<h1>Intro: A/B</h1><p>x</p><h2>Sub</h2><h3>Deep</h3><h2>Sub</h2><h1>Ünï &amp; co</h1>"
    assert gql_wiki.wiki_toc(html) == (
        '<div class="toc"><ul><li class="wiki_intro.3A_a.2Fb"><a href="#wiki_intro.3A_a.2Fb">Intro: A/B</a></li>'
        '<li class="toc_child"><ul><li class="wiki_sub"><a href="#wiki_sub">Sub</a></li>'
        '<li class="toc_child"><ul><li class="wiki_deep"><a href="#wiki_deep">Deep</a></li></ul></li>'
        '<li class="wiki_sub2"><a href="#wiki_sub2">Sub</a></li></ul></li>'
        '<li class="wiki_.C3.BCn.C3.AF_.26_co"><a href="#wiki_.C3.BCn.C3.AF_.26_co">Ünï &amp; co</a></li></ul></div>'
        '<h1 id="wiki_intro.3A_a.2Fb">Intro: A/B</h1><p>x</p><h2 id="wiki_sub">Sub</h2><h3 id="wiki_deep">Deep</h3>'
        '<h2 id="wiki_sub2">Sub</h2><h1 id="wiki_.C3.BCn.C3.AF_.26_co">Ünï &amp; co</h1>')
    assert gql_wiki.wiki_toc("<p>no headings</p>") == "<p>no headings</p>"


def test_missing_page_is_404():
    with patch.object(gql_wiki, "gql_query", lambda *a, **k: _node("PAGE_NOT_FOUND")):
        with pytest.raises(UpstreamError) as e:
            gql_wiki.fetch_gql_wiki("x", "nope")
    assert e.value.status == 404


def test_unavailable_subreddit_is_403():
    with patch.object(gql_wiki, "gql_query", lambda *a, **k: _node(typename="UnavailableSubreddit")):
        with pytest.raises(UpstreamError) as e:
            gql_wiki.fetch_gql_wiki("x", "index")
    assert e.value.status == 403


def test_fetch_wiki_prefers_graphql(monkeypatch):
    monkeypatch.setattr(subreddit_routes, "fetch_gql_wiki",
                        lambda sub, page: {"content_html": "<p>gql</p>", "revision_date": None})
    assert subreddit_routes.fetch_wiki("x", "index")["content_html"] == "<p>gql</p>"
