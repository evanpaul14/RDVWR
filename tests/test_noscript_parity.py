"""Rendered no-JS controls must retain the capabilities and settings of the JS UI."""
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit, urlencode

import pytest
from bs4 import BeautifulSoup
from app import app
from core import helpers, template_helpers
from core.ns_prefs import PREFS
from parsing.media_detection import process_post
from routes import page_data, pages, ns_settings
from reddit import login
from tests.test_noscript import _noscript
from routes.comments import _parse_comment_fields


@pytest.fixture
def client(monkeypatch):
    helpers._fetch_cache.clear()
    helpers._view_cache.clear()
    monkeypatch.setattr(login, 'request_account_active', lambda: False)
    monkeypatch.setattr(login, 'login_status', lambda: None)
    monkeypatch.setattr(login, 'request_cookie_header', lambda: None)
    monkeypatch.setattr(pages, 'DISABLE_PERSONALIZED_HOME', False)
    monkeypatch.setattr(ns_settings, 'DISABLE_PERSONALIZED_HOME', False)
    monkeypatch.setattr(ns_settings, 'DISABLE_NSFW', False)
    monkeypatch.setattr(template_helpers, 'DISABLE_PERSONALIZED_HOME', False)
    monkeypatch.setattr(template_helpers, 'DISABLE_NSFW', False)
    monkeypatch.setattr(template_helpers, 'DISABLE_DOWNLOADS', False)
    c = app.test_client()
    c.environ_base['HTTP_SEC_FETCH_SITE'] = 'same-origin'
    return c


@pytest.fixture
def logged_in(monkeypatch):
    monkeypatch.setattr(login, 'request_account_active', lambda: True)
    monkeypatch.setattr(login, 'username', lambda: 'tester')
    monkeypatch.setattr(login, 'login_status', lambda: {'available': True, 'username': 'tester'})
    monkeypatch.setattr(login, 'request_cookie_header', lambda: 'fixture=1')


def soup(response):
    assert response.status_code == 200
    return BeautifulSoup(_noscript(response), 'html.parser')


def post(**overrides):
    return process_post({'id': 'abc123', 'subreddit': 'testsub', 'title': 'Post', 'author': 'tester', **overrides})


def test_all_offered_settings_round_trip_and_reset_to_deployment_defaults(client, logged_in, monkeypatch):
    monkeypatch.setattr(ns_settings, 'NSFW_ONLY_SEARCH', True)
    monkeypatch.setattr(template_helpers, 'NSFW_ONLY_SEARCH', True)
    values = {name: '1' for name, pref in PREFS.items() if pref.allowed is None}
    values.update(theme='light', layout='compact', sub_sort='top', sub_time='week', comment_sort='new',
                  home_feed='subscribed', personalized_home_shown='1', next='/settings')
    response = client.post('/settings', data=values)
    assert response.status_code == 302
    form = soup(client.get('/settings')).select_one('.ns-settings-form')
    for name in PREFS:
        control = form.find(attrs={'name': name})
        assert control is not None, name
        assert (control.select_one('option[selected]')['value'] if control.name == 'select' else control.has_attr('checked')) == (values[name] if control.name == 'select' else True)
    # An unchecked checkbox must override a deployment default that is on.
    client.post('/settings', data={'theme': 'dark', 'layout': 'card', 'personalized_home_shown': '1'})
    assert not soup(client.get('/settings')).find(attrs={'name': 'show_awards'}).has_attr('checked')
    monkeypatch.setitem(helpers.DEFAULT_SETTINGS, 'showAvatars', True)
    monkeypatch.setitem(helpers.DEFAULT_SETTINGS, 'layout', 'minimal')
    monkeypatch.setitem(helpers.DEFAULT_SETTINGS, 'homeFeed', 'popular')
    response = client.post('/settings', data={'reset': '1', 'next': '/settings'})
    assert response.status_code == 302
    assert all(f'{pref.cookie}=;' in '\n'.join(response.headers.getlist('Set-Cookie')) for pref in PREFS.values())
    reset = soup(client.get('/settings'))
    assert reset.find(attrs={'name': 'show_avatars'}).has_attr('checked')
    assert reset.select_one('[name=layout] option[selected]')['value'] == 'minimal'
    assert reset.select_one('[name=home_feed] option[selected]')['value'] == 'popular'


def test_unavailable_settings_are_hidden_and_not_silently_reset(client, logged_in, monkeypatch):
    client.set_cookie('ns_home_feed', 'subscribed')
    client.set_cookie('ns_personalized_home', '0')
    client.set_cookie('ns_nsfw_only_search', '0')
    monkeypatch.setattr(ns_settings, 'DISABLE_PERSONALIZED_HOME', True)
    monkeypatch.setattr(template_helpers, 'DISABLE_PERSONALIZED_HOME', True)
    monkeypatch.setattr(ns_settings, 'DISABLE_NSFW', True)
    monkeypatch.setattr(template_helpers, 'DISABLE_NSFW', True)
    html = soup(client.get('/settings'))
    assert html.find(attrs={'name': 'home_feed'}) is None
    assert html.find(attrs={'name': 'personalized_home'}) is None
    assert html.find(attrs={'name': 'nsfw_hide'}) is None
    response = client.post('/settings', data={'theme': 'light', 'personalized_home_shown': '1', 'home_feed': 'popular'})
    cookies = '\n'.join(response.headers.getlist('Set-Cookie'))
    assert 'ns_home_feed=' not in cookies and 'ns_personalized_home=' not in cookies and 'ns_nsfw_only_search=' not in cookies


def test_public_settings_do_not_offer_account_home_choices(client):
    html = soup(client.get('/settings'))
    assert html.find(attrs={'name': 'home_feed'}) is None
    assert html.find(attrs={'name': 'show_avatars'}) is not None
    assert html.find(attrs={'name': 'reset'}) is not None
    assert html.find(attrs={'name': 'redditCookies'}) is None


@pytest.mark.parametrize('name', ['theme', 'layout', 'sub_sort', 'sub_time', 'comment_sort', 'home_feed'])
def test_invalid_setting_values_revert_to_configured_defaults(client, logged_in, name):
    pref = PREFS[name]
    client.set_cookie(pref.cookie, 'bogus')
    control = soup(client.get('/settings')).find(attrs={'name': name})
    assert control.select_one('option[selected]')['value'] == helpers.DEFAULT_SETTINGS[pref.default]
    result = client.post('/settings', data={name: 'bogus', 'personalized_home_shown': '1'})
    assert pref.cookie + '=;' in '\n'.join(result.headers.getlist('Set-Cookie'))


@pytest.mark.parametrize('mode', ['personalized', 'popular', 'subscribed'])
def test_home_feed_choice_selects_the_correct_source(client, logged_in, monkeypatch, mode):
    client.set_cookie('ns_home_feed', mode)
    personal = Mock(return_value={'posts': [post(title='Personalized source')], 'after': 'next'})
    popular = Mock(return_value={'posts': [post(title='Popular source')], 'after': 'next'})
    subscribed = Mock(return_value={'posts': [post(title='Subscribed source')], 'after': 'next'})
    monkeypatch.setattr(page_data, 'fetch_personalized_home', personal)
    monkeypatch.setattr(page_data, 'fetch_feed', popular)
    monkeypatch.setattr(page_data, 'fetch_subscriptions', Mock(return_value=['testsub']))
    monkeypatch.setattr(page_data, 'fetch_frontpage', subscribed)
    monkeypatch.setattr(page_data, 'fetch_about', Mock(return_value={}))
    html = soup(client.get('/home/top?t=week'))
    assert mode.capitalize() + ' source' in html.get_text()
    {'personalized': personal, 'popular': popular, 'subscribed': subscribed}[mode].assert_called_once()
    if mode == 'subscribed':
        assert urlsplit(html.select_one('[rel=next]')['href']).path == '/home/top'
    if mode == 'popular':
        assert popular.call_args.args[:3] == ('popular', 'top', 'week')


def test_personalized_paging_preserves_distance_in_both_directions(client, logged_in, monkeypatch):
    client.set_cookie('ns_home_feed', 'personalized')
    fetch = Mock(return_value={'posts': [], 'after': 'next'})
    monkeypatch.setattr(page_data, 'fetch_personalized_home', fetch)
    html = soup(client.get('/home/top?t=week&after=current&distance=12&prev=earlier'))
    assert fetch.call_args.kwargs['distance'] == 12
    assert parse_qs(urlsplit(html.select_one('[rel=next]')['href']).query)['distance'] == ['16']
    previous = parse_qs(urlsplit(html.select_one('[rel=prev]')['href']).query)
    assert previous['distance'] == ['8'] and previous['after'] == ['earlier']


def test_turning_personalized_home_off_ignores_the_selected_home_source(client, logged_in, monkeypatch):
    client.set_cookie('ns_home_feed', 'popular')
    client.set_cookie('ns_personalized_home', '0')
    fetch = Mock(return_value={'posts': [], 'after': None})
    personal = Mock(side_effect=AssertionError('personalization should be off'))
    monkeypatch.setattr(page_data, 'fetch_frontpage', fetch)
    monkeypatch.setattr(page_data, 'fetch_personalized_home', personal)
    assert client.get('/home').status_code == 200
    fetch.assert_called_once()
    personal.assert_not_called()


@pytest.mark.parametrize('cursor', ['arc:1700000000', 'gql2:eyJwb3N0cyI6InQzX2FiYyJ9', 'ab+/cd=='])
def test_pager_accepts_archive_and_graphql_cursors(cursor):
    with app.test_request_context('/user/test?' + urlencode({'after': cursor, 'prev': 'earlier'})):
        previous, following = template_helpers.pager_urls('/user/test?after=next')
    assert parse_qs(urlsplit(previous).query)['after'] == ['earlier']
    assert parse_qs(urlsplit(following).query)['prev'] == ['earlier,' + cursor]


@pytest.mark.parametrize('layout, enabled, expected', [('card', True, True), ('compact', True, True), ('minimal', True, False), ('card', False, False)])
@pytest.mark.parametrize('more', [False, True])
def test_avatar_setting_controls_fetching_and_rendering_on_all_comment_pages(client, monkeypatch, layout, enabled, expected, more):
    client.set_cookie('ns_layout', layout)
    client.set_cookie('ns_show_avatars', '1' if enabled else '0')
    comment = _parse_comment_fields({'id': 'def123', 'author': 'person', 'body': 'hi'})
    comment['author_icon'] = 'https://styles.redditmedia.com/avatar.png'
    fetch = Mock(return_value={'post': post(), 'comments': [comment]})
    expand = Mock(return_value={'comments': [comment]})
    monkeypatch.setattr(page_data, 'fetch_comments', fetch)
    monkeypatch.setattr(page_data, 'fetch_morechildren', expand)
    html = soup(client.get('/r/testsub/comments/abc123' + ('?more=def123' if more else '')))
    assert bool(html.select_one('.comment-avatar')) == expected
    assert fetch.call_args.kwargs['with_avatars'] == expected
    if more:
        assert expand.call_args.kwargs['with_avatars'] == expected


@pytest.mark.parametrize('kind', ['preview', 'video', 'gallery'])
def test_proxied_download_links_use_real_media_urls(client, monkeypatch, kind):
    p = post(is_self=False, domain='i.redd.it', url='https://i.redd.it/file.png')
    if kind == 'preview':
        p['preview_img'] = '/api/img?' + urlencode({'url': 'https://preview.redd.it/file.png?s=1'})
    elif kind == 'video':
        p.update(is_video=True, video_url='https://v.redd.it/vid/DASH_720.mp4', hls_url='/api/m/v.redd.it/vid/HLSPlaylist.m3u8?f=sd')
    else:
        p['gallery'] = [{'url': '/api/m/i.redd.it/file.png', 'width': 1, 'height': 1, 'caption': ''}]
    monkeypatch.setattr(page_data, 'fetch_comments', Mock(return_value={'post': p, 'comments': []}))
    html = soup(client.get('/r/testsub/comments/abc123'))
    link = html.select_one('a[download]')['href']
    args = parse_qs(urlsplit(link).query)
    if kind == 'preview':
        assert args['url'] == ['https://preview.redd.it/file.png?s=1'] and args['filename'] == ['abc123.png']
    elif kind == 'video':
        assert args['hls'] == ['https://v.redd.it/vid/HLSPlaylist.m3u8?f=sd']
    else:
        assert args['urls'] == ['https://i.redd.it/file.png']


def test_quarantine_opt_in_survives_sort_and_time_controls(client, monkeypatch):
    monkeypatch.setattr(page_data, 'fetch_feed', Mock(return_value={'posts': [], 'after': None}))
    for name, result in [('fetch_about', {}), ('fetch_rules', []), ('fetch_widgets', [])]:
        monkeypatch.setattr(page_data, name, Mock(return_value=result))
    html = soup(client.get('/r/testsub/top?t=week&quarantine_opt_in=1'))
    for link in html.select('.sort-bar a.sort-btn'):
        if '/wiki' not in link['href']:
            assert parse_qs(urlsplit(link['href']).query)['quarantine_opt_in'] == ['1']
    assert html.select_one('.ns-time-form [name=quarantine_opt_in]')['value'] == '1'


def test_plain_post_text_and_rich_account_comments_are_not_lost(client, logged_in, monkeypatch):
    p = post(is_self=True, selftext='Text without an HTML field')
    monkeypatch.setattr(page_data, 'fetch_comments', Mock(return_value={'post': p, 'comments': []}))
    assert 'Text without an HTML field' in soup(client.get('/r/testsub/comments/abc123')).select_one('.pv-body').get_text()
    monkeypatch.setattr(page_data, 'fetch_user_about', Mock(return_value={}))
    monkeypatch.setattr(page_data, 'fetch_user_comments', Mock(return_value={'comments': [{'id': 'def123', 'subreddit': 'testsub', 'link_id': 'abc123', 'body': 'raw', 'body_html': '<p><strong>formatted</strong></p>', 'score': 1}], 'after': None}))
    assert soup(client.get('/account?tab=comments')).select_one('.ucc-body strong').get_text() == 'formatted'


def test_profile_details_and_trophies_are_available_without_eager_trophy_requests(client, monkeypatch):
    monkeypatch.setattr(page_data, 'fetch_user_about', Mock(return_value={'name': 'tester', 'is_mod': True, 'karma_total': 10, 'created_utc': 1700000000}))
    monkeypatch.setattr(page_data, 'fetch_user_overview', Mock(return_value={'items': [{'type': 'post', 'data': post()}], 'after': 'next'}))
    trophies = Mock(return_value=[{'name': 'Trophy', 'description': 'Description', 'icon': 'https://www.redditstatic.com/trophy.png'}])
    monkeypatch.setattr(page_data, 'fetch_user_trophies', trophies)
    html = soup(client.get('/user/tester'))
    trophies.assert_not_called()
    assert 'Moderator' in html.get_text() and 'testsub' in html.get_text()
    href = html.find('a', string='Load trophies')['href']
    detail = soup(client.get(href))
    assert detail.select_one('.sidebar-trophy-name').get_text() == 'Trophy'
    trophies.assert_called_once()
    assert parse_qs(urlsplit(detail.select_one('[rel=next]')['href']).query)['about'] == ['1']


def test_poll_results_and_wiki_metadata_match_js_pages(client, monkeypatch):
    p = post(poll_data={'total_vote_count': 8, 'voting_end_timestamp': 9999999999999,
                        'options': [{'id': 'a', 'text': 'Option', 'vote_count': 1}]})
    monkeypatch.setattr(page_data, 'fetch_comments', Mock(return_value={'post': p, 'comments': []}))
    poll = soup(client.get('/r/testsub/comments/abc123')).select_one('.poll-widget')
    assert poll.select_one('.poll-pct').get_text() == '13%'
    assert poll.select_one('.poll-status').get_text() == 'Poll open'
    monkeypatch.setattr(page_data, 'fetch_wiki', Mock(return_value={'content_html': '<p>Wiki</p>', 'revision_date': 1700000000}))
    assert 'Last revised' in soup(client.get('/r/testsub/wiki')).select_one('.wiki-meta').get_text()


@pytest.mark.parametrize('media', [
    {'gif_url': 'https://example.com/file.mp4', 'gif_is_video': True},
    {'preview_img': 'https://i.redd.it/file.png'},
    {'youtube_id': 'abcdefghijk'},
    {'gallery': [{'url': 'https://i.redd.it/file.png', 'caption': ''}]},
    {'is_video': True, 'hls_url': '/api/m/v.redd.it/a/HLSPlaylist.m3u8', 'video_url': 'https://v.redd.it/a/DASH_720.mp4'},
])
def test_media_privacy_setting_prevents_unproxied_embeds(client, monkeypatch, media):
    client.set_cookie('ns_link_external_media', '1')
    p = {**post(url='https://example.com/source'), **media}
    monkeypatch.setattr(page_data, 'fetch_comments', Mock(return_value={'post': p, 'comments': []}))
    html = soup(client.get('/r/testsub/comments/abc123'))
    assert not html.select('video, iframe, .pv-media img')
    assert html.select_one('.pv-article-link') is not None


def test_privacy_allows_local_proxy_media_and_minimal_layout_uses_plain_video_links(client, monkeypatch):
    client.set_cookie('ns_link_external_media', '1')
    p = post(url='https://i.redd.it/file.png')
    p['preview_img'] = '/api/img?url=https%3A%2F%2Fi.redd.it%2Ffile.png'
    fetch = Mock(return_value={'post': p, 'comments': []})
    monkeypatch.setattr(page_data, 'fetch_comments', fetch)
    assert soup(client.get('/r/testsub/comments/abc123')).select_one('.pv-media img')['src'] == p['preview_img']
    client.set_cookie('ns_layout', 'minimal')
    p.update(is_video=True, video_url='/api/m/v.redd.it/a/DASH_720.mp4', hls_url='/api/m/v.redd.it/a/HLSPlaylist.m3u8')
    html = soup(client.get('/r/testsub/comments/abc123'))
    assert not html.select('video, iframe')
    assert html.select_one('.pv-article-link') is not None


def test_hide_filter_and_award_setting_apply_to_own_profile_posts(client, logged_in, monkeypatch):
    client.set_cookie('ns_nsfw_hide', '1')
    client.set_cookie('ns_show_awards', '0')
    visible = post()
    visible['awards'] = [{'name': 'Test award', 'icon': 'https://example.com/award.png', 'count': 1}]
    hidden = post(id='hidden', title='Hidden post', over_18=True)
    monkeypatch.setattr(page_data, 'fetch_user_about', Mock(return_value={}))
    monkeypatch.setattr(page_data, 'fetch_user_posts', Mock(return_value={'posts': [visible, hidden], 'after': None}))
    html = soup(client.get('/account'))
    assert not html.select('.awards')
    assert not html.select('[data-post-id=hidden]')
    assert html.select('[data-post-id=abc123]')
