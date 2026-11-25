"""Visitor content filters (core/filters.py): parsing, matching, the /settings form, and
hiding filtered posts on the no-JS pages."""
import pytest
from bs4 import BeautifulSoup

from app import app
from core import filters
from core.filters import normalize_subs, normalize_users, normalize_keywords, post_is_filtered
from parsing.media_detection import process_post
from routes import page_data
from tests.test_noscript import _noscript


def _filters(subs=(), users=(), keywords=()):
    return {'filter_subs': list(subs), 'filter_users': list(users), 'filter_keywords': list(keywords)}


def _post(title='A title', sub='pics', author='someone', pid='p1'):
    return process_post({'id': pid, 'title': title, 'subreddit': sub, 'author': author})


class TestNormalize:
    def test_subs_accept_prefixes_and_separators_and_dedupe(self):
        assert normalize_subs('r/Funny, /r/pics\nfunny  bad-name!') == ['Funny', 'pics']

    def test_users_accept_prefixes(self):
        assert normalize_users('u/spez /user/some-one') == ['spez', 'some-one']

    def test_keywords_are_one_per_line_and_collapse_whitespace(self):
        assert normalize_keywords('  game  of thrones \n\nspoiler, alert\nSPOILER, ALERT') == ['game of thrones', 'spoiler, alert']

    def test_lists_are_capped(self):
        assert len(normalize_subs(' '.join(f's{i}' for i in range(500)))) == filters.MAX_ENTRIES


class TestMatching:
    def test_sub_filter_hides_elsewhere_but_not_on_the_sub_itself(self):
        f = _filters(subs=['Pics'])
        assert post_is_filtered(_post(sub='pics'), f, '/r/popular')
        assert post_is_filtered(_post(sub='pics'), f, '/r/pics+funny')
        assert not post_is_filtered(_post(sub='pics'), f, '/r/pics/top')

    def test_user_filter_hides_elsewhere_but_not_on_their_profile(self):
        f = _filters(users=['Spez'])
        assert post_is_filtered(_post(author='spez'), f, '/')
        assert not post_is_filtered(_post(author='spez'), f, '/user/spez')
        assert not post_is_filtered(_post(author='spez'), f, '/u/SPEZ/posts')

    def test_keywords_match_whole_words_case_insensitively(self):
        f = _filters(keywords=['cat', 'c++'])
        assert post_is_filtered(_post(title='My CAT sleeps'), f, '/')
        assert not post_is_filtered(_post(title='Category theory'), f, '/')
        assert post_is_filtered(_post(title='Learning C++ today'), f, '/')


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        c.environ_base = {'HTTP_SEC_FETCH_SITE': 'same-origin'}
        yield c


def test_settings_form_round_trips_filters_and_reset_clears_them(client):
    resp = client.post('/settings', data={'filter_subs': 'r/funny pics', 'filter_users': 'spez',
                                          'filter_keywords': 'game of thrones\nspoiler', 'next': '/'})
    assert resp.status_code == 302
    form = BeautifulSoup(_noscript(client.get('/settings')), 'html.parser')
    assert form.find('textarea', attrs={'name': 'filter_subs'}).text == 'funny\npics'
    assert form.find('textarea', attrs={'name': 'filter_users'}).text == 'spez'
    assert form.find('textarea', attrs={'name': 'filter_keywords'}).text == 'game of thrones\nspoiler'
    resp = client.post('/settings', data={'reset': '1', 'next': '/'})
    cookies = '\n'.join(resp.headers.getlist('Set-Cookie'))
    assert all(f'{f.cookie}=;' in cookies for f in filters.FILTERS.values())


def test_filtered_posts_are_left_out_of_noscript_feeds(client, monkeypatch):
    posts = [_post('Keep me', 'pics', 'a', 'k1'), _post('Spoiler inside', 'pics', 'b', 'k2'),
             _post('From funny', 'funny', 'c', 'k3'), _post('By spez', 'pics', 'spez', 'k4')]
    monkeypatch.setattr(page_data, 'fetch_feed', lambda *a, **kw: {'posts': posts, 'after': None})
    client.post('/settings', data={'filter_subs': 'funny', 'filter_users': 'spez', 'filter_keywords': 'spoiler'})
    html = _noscript(client.get('/r/pics+funny'))
    assert 'Keep me' in html
    assert 'Spoiler inside' not in html and 'From funny' not in html and 'By spez' not in html
    # Filters are kept in the hydration payload too; the JS app filters on render.
    assert 'From funny' in client.get('/r/pics+funny').get_data(as_text=True)
