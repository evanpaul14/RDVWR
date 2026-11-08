"""Regression coverage for content flags observed in live Shreddit partial HTML."""
from pathlib import Path
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from app import app
from parsing import media_detection
from parsing.shreddit import _parse_shreddit_post
from routes import home, page_data

HTML = (Path(__file__).parent / 'fixtures' / 'shreddit_feed_flags.html').read_text()


@pytest.mark.parametrize('flags, expected', [
    ('nsfw', True), ('subreddit-nsfw', True), ('is-nsfw', True),
    ('nsfw subreddit-nsfw', True), ('is-not-brand-safe', False), ('', False),
])
def test_current_and_legacy_boolean_flags(flags, expected):
    el = BeautifulSoup(f'<shreddit-post id="t3_fixture" {flags}></shreddit-post>', 'html.parser').find('shreddit-post')
    assert _parse_shreddit_post(el)['over_18'] is expected


@pytest.mark.parametrize('disabled', [True, False])
def test_personalized_filter_preserves_cursor_and_skips_enrichment_for_filtered_posts(monkeypatch, disabled):
    monkeypatch.setattr(media_detection, 'DISABLE_NSFW', disabled)
    monkeypatch.setattr(home.cffi_requests, 'get', Mock(return_value=Mock(status_code=200, text=HTML)))
    enrich = Mock(return_value={})
    monkeypatch.setattr(home, 'fetch_posts_by_id', enrich)
    monkeypatch.setattr(home, 'hydrate_linked_posts', Mock())
    result = home.fetch_personalized_home('fixture=1', 'best')
    expected = ['safe'] if disabled else ['safe', 'flagged']
    assert [p['id'] for p in result['posts']] == expected
    assert result['after'] == 't3_next'
    enrich.assert_called_once_with(expected)


def test_filter_applies_to_api_and_server_rendered_home(monkeypatch):
    monkeypatch.setattr(media_detection, 'DISABLE_NSFW', True)
    monkeypatch.setattr(home.cffi_requests, 'get', Mock(return_value=Mock(status_code=200, text=HTML)))
    monkeypatch.setattr(home, 'fetch_posts_by_id', Mock(return_value={}))
    monkeypatch.setattr(home, 'hydrate_linked_posts', Mock())
    result = app.test_client().get('/api/home', headers={
        'Sec-Fetch-Site': 'same-origin', 'X-Reddit-Cookie': 'fixture=1',
    })
    assert result.status_code == 200
    assert [p['id'] for p in result.get_json()['posts']] == ['safe']
    with app.test_request_context('/'):
        ctx = page_data.build_home('best', 'all', '', cookie='fixture=1')
    assert [p['id'] for p in ctx['page']['posts']] == ['safe']
    assert [p['id'] for p in ctx['initial_listing']['data']['posts']] == ['safe']
