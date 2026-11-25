"""Atom feeds (routes/rss.py) for subreddits and user profiles."""
import xml.etree.ElementTree as ET
import pytest

from app import app
from core.helpers import UpstreamError
from parsing.media_detection import process_post
from routes import rss

NS = {'a': 'http://www.w3.org/2005/Atom'}


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _posts():
    return [
        process_post({'id': 'abc1', 'title': 'Self <post> & more', 'subreddit': 'pics', 'author': 'alice',
                      'is_self': True, 'selftext': 'See r/funny and [here](/r/pics)', 'created_utc': 1700000000}),
        process_post({'id': 'abc2', 'title': 'A link', 'subreddit': 'pics', 'author': 'bob',
                      'url': 'https://example.com/x', 'domain': 'example.com', 'created_utc': 1700000100}),
    ]


def test_subreddit_feed_is_valid_atom_linking_to_this_app(client, monkeypatch):
    calls = []
    monkeypatch.setattr(rss, 'fetch_feed', lambda *a: calls.append(a) or {'posts': _posts(), 'after': None})
    resp = client.get('/r/pics/top.rss?t=week')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/atom+xml'
    assert calls == [('pics', 'top', 'week', '', False)]
    feed = ET.fromstring(resp.data)
    assert feed.find('a:title', NS).text == 'r/pics — top'
    entries = feed.findall('a:entry', NS)
    assert [e.find('a:title', NS).text for e in entries] == ['Self <post> & more', 'A link']
    assert entries[0].find('a:link', NS).get('href') == 'http://localhost/r/pics/comments/abc1'
    assert entries[0].find('a:author/a:name', NS).text == 'u/alice'
    content = entries[0].find('a:content', NS).text
    assert 'href="http://localhost/r/pics"' in content
    assert 'href="https://example.com/x"' in entries[1].find('a:content', NS).text
    assert feed.find('a:updated', NS).text == '2023-11-14T22:15:00Z'


def test_default_sort_and_invalid_time(client, monkeypatch):
    calls = []
    monkeypatch.setattr(rss, 'fetch_feed', lambda *a: calls.append(a) or {'posts': [], 'after': None})
    assert client.get('/r/pics+funny.rss?t=bogus').status_code == 200
    assert calls == [('pics+funny', 'hot', '', '', False)]
    client.get('/r/q.rss?quarantine_opt_in=1')
    assert calls[-1] == ('q', 'hot', '', '', True)


@pytest.mark.parametrize('path', ['/user/alice.rss', '/u/alice.rss', '/user/alice/submitted.rss'])
def test_user_feed(client, monkeypatch, path):
    monkeypatch.setattr(rss, 'fetch_user_posts', lambda u: {'posts': _posts(), 'after': None})
    resp = client.get(path)
    assert resp.status_code == 200
    assert ET.fromstring(resp.data).find('a:title', NS).text == 'u/alice'


def test_upstream_errors_and_unknown_paths(client, monkeypatch):
    def missing(*a):
        raise UpstreamError('Subreddit not found', status=404, state='not_found')
    monkeypatch.setattr(rss, 'fetch_feed', missing)
    assert client.get('/r/nope.rss').status_code == 404
    assert client.get('/r/pics/comments/abc.rss').status_code == 404
    assert client.get('/r/bad name.rss').status_code == 404


def test_subreddit_page_advertises_its_feed(client, monkeypatch):
    from routes import page_data
    monkeypatch.setattr(page_data, 'fetch_feed', lambda *a, **kw: {'posts': [], 'after': None})
    html = client.get('/r/pics+funny', environ_base={'HTTP_SEC_FETCH_SITE': 'same-origin'}).get_data(as_text=True)
    assert '<link rel="alternate" type="application/atom+xml" title="r/pics+funny" href="/r/pics+funny.rss">' in html
    assert 'href="/r/pics+funny/hot.rss"' in html
