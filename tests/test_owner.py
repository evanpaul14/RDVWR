import pytest

import reddit_login
import reddit_owner
from app import app

PROXIED = {'X-Forwarded-For': '1.2.3.4', 'X-Real-IP': '192.168.1.5', 'Sec-Fetch-Site': 'same-origin'}


@pytest.fixture
def owner_on(monkeypatch):
    monkeypatch.setattr(reddit_login, 'ENABLED', True)
    monkeypatch.setattr(reddit_owner, 'KEY', 'k' * 32)
    reddit_owner._fails.clear()


def test_owner_cookie_grants_local_access(owner_on):
    c = app.test_client()
    assert c.post('/auth/owner', data={'key': 'nope'}, headers=PROXIED).status_code == 403
    ok = c.post('/auth/owner', data={'key': 'k' * 32}, headers=PROXIED)
    assert ok.status_code == 302
    assert 'HttpOnly' in ok.headers['Set-Cookie']
    cookie = ok.headers['Set-Cookie'].split(';')[0]
    with app.test_request_context('/', headers=PROXIED):
        assert not reddit_login.is_local_request()
    with app.test_request_context('/', headers={**PROXIED, 'Cookie': cookie}):
        assert reddit_login.is_local_request()


def test_lockout_after_repeated_failures(owner_on):
    c = app.test_client()
    for _ in range(5):
        c.post('/auth/owner', data={'key': 'nope'}, headers=PROXIED)
    assert c.post('/auth/owner', data={'key': 'k' * 32}, headers=PROXIED).status_code == 403


def test_owner_page_404_without_key(monkeypatch):
    monkeypatch.setattr(reddit_login, 'ENABLED', True)
    monkeypatch.setattr(reddit_owner, 'KEY', '')
    assert app.test_client().get('/auth/owner').status_code == 404
