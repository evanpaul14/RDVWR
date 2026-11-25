"""Settings backup codes and restore links (core/backup.py, /settings/restore)."""
import pytest
from bs4 import BeautifulSoup

from app import app
from core import backup
from tests.test_noscript import _noscript


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        c.environ_base = {'HTTP_SEC_FETCH_SITE': 'same-origin'}
        yield c


def _code(settings, subscriptions=()):
    return backup.encode({'v': 1, 'settings': settings, 'subscriptions': list(subscriptions)})


class TestCodec:
    def test_round_trip_keeps_unicode(self):
        data = {'v': 1, 'settings': {'filterKeywords': ['café', 'naïve']}, 'subscriptions': ['pics']}
        assert backup.decode(backup.encode(data)) == data

    def test_accepts_a_full_restore_link(self):
        code = _code({'theme': 'light'})
        assert backup.decode(f'https://example.com/rdvwr/settings/restore?data={code}')['settings'] == {'theme': 'light'}
        assert backup.decode(f'  /settings/restore?data={code}\n')['settings'] == {'theme': 'light'}

    @pytest.mark.parametrize('bad', ['', 'not base64!', backup.encode({'v': 2, 'settings': {}}),
                                     backup.encode(['list']), backup.encode({'v': 1, 'settings': []}),
                                     backup.encode({'v': 1, 'settings': {}, 'subscriptions': 'pics'}),
                                     'https://example.com/settings/restore', 'A' * 30000])
    def test_rejects_invalid_codes(self, bad):
        assert backup.decode(bad) is None


def test_settings_page_offers_link_code_and_import(client):
    client.set_cookie('ns_theme', 'light')
    page = BeautifulSoup(_noscript(client.get('/settings')), 'html.parser')
    link = page.find('input', attrs={'readonly': True})['value']
    assert link.startswith('http://localhost/settings/restore?data=')
    data = backup.decode(link)
    assert data['settings']['theme'] == 'light'
    assert data['settings']['filterSubs'] == []
    assert 'redditCookies' not in data['settings']
    assert page.find('form', attrs={'action': '/settings/restore'}).find('textarea', attrs={'name': 'data'})


def test_restore_link_asks_before_applying(client):
    code = _code({'theme': 'light', 'layout': 'compact', 'filterSubs': ['funny', 'pics']}, ['pics'])
    resp = client.get(f'/settings/restore?data={code}')
    assert resp.status_code == 200
    assert not resp.headers.getlist('Set-Cookie') or all('ns_' not in c for c in resp.headers.getlist('Set-Cookie'))
    html = _noscript(resp)
    assert 'restores 2 settings and 2 filter entries' in html
    assert '1 subscription, which are only restored with JavaScript on' in html
    form = BeautifulSoup(html, 'html.parser').find('form', attrs={'action': '/settings/restore'})
    assert form.find('input', attrs={'name': 'data'})['value'] == code


def test_invalid_restore_link(client):
    assert 'invalid or incomplete' in _noscript(client.get('/settings/restore?data=garbage'))


def test_import_applies_valid_values_and_skips_invalid_ones(client):
    client.set_cookie('ns_sub_sort', 'new')
    code = _code({'theme': 'light', 'layout': 'bogus', 'showAvatars': True, 'nsfwBlur': 'yes',
                  'filterUsers': ['u/spez'], 'filterKeywords': ['spoiler'], 'redditCookies': 'x=1'})
    resp = client.post('/settings/restore', data={'data': code})
    assert resp.status_code == 302 and resp.headers['Location'] == '/settings?import=ok'
    cookies = '\n'.join(resp.headers.getlist('Set-Cookie'))
    assert 'ns_theme=light' in cookies and 'ns_show_avatars=1' in cookies
    assert 'ns_layout' not in cookies and 'ns_nsfw_blur' not in cookies and 'ns_sub_sort' not in cookies
    page = BeautifulSoup(_noscript(client.get('/settings?import=ok')), 'html.parser')
    assert 'Settings restored' in page.text
    assert page.find('textarea', attrs={'name': 'filter_users'}).text == 'spez'
    assert page.select_one('[name=sub_sort] option[selected]')['value'] == 'new'


def test_import_rejects_garbage(client):
    resp = client.post('/settings/restore', data={'data': 'nope'})
    assert resp.headers['Location'] == '/settings?import=invalid'
    assert not any('ns_' in c for c in resp.headers.getlist('Set-Cookie'))
    assert 'invalid or incomplete' in _noscript(client.get('/settings?import=invalid'))


def test_export_then_import_round_trips_between_browsers(client):
    client.post('/settings', data={'theme': 'light', 'layout': 'minimal', 'show_awards': '1',
                                   'filter_subs': 'funny', 'filter_keywords': 'game of thrones'})
    link = BeautifulSoup(_noscript(client.get('/settings')), 'html.parser').find('input', attrs={'readonly': True})['value']
    with app.test_client() as other:
        other.environ_base = {'HTTP_SEC_FETCH_SITE': 'same-origin'}
        other.post('/settings/restore', data={'data': link})
        page = BeautifulSoup(_noscript(other.get('/settings')), 'html.parser')
    assert page.select_one('[name=theme] option[selected]')['value'] == 'light'
    assert page.select_one('[name=layout] option[selected]')['value'] == 'minimal'
    assert page.find('textarea', attrs={'name': 'filter_subs'}).text == 'funny'
    assert page.find('textarea', attrs={'name': 'filter_keywords'}).text == 'game of thrones'
