import pytest
from unittest.mock import patch

from app import app
from core import ratelimit


@pytest.fixture(autouse=True)
def _fresh_counters():
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def client():
    app.config['TESTING'] = True
    return app.test_client()


def _get(client, path, ip='1.1.1.1', **headers):
    return client.get(path, environ_base={'REMOTE_ADDR': ip}, headers=headers)


def test_over_limit_gets_429_with_retry_after(client):
    with patch.object(ratelimit, 'RATE_LIMIT', 3):
        codes = [_get(client, "/x").status_code for _ in range(4)]
    assert codes[:3] != [429] * 3 and codes[3] == 429
    resp = None
    with patch.object(ratelimit, 'RATE_LIMIT', 3):
        resp = _get(client, '/api/search')
    assert resp.status_code == 429
    assert resp.is_json and int(resp.headers['Retry-After']) >= 1


def test_limit_is_per_client(client):
    with patch.object(ratelimit, 'RATE_LIMIT', 2):
        for _ in range(3):
            _get(client, '/x', ip='1.1.1.1')
        assert _get(client, '/x', ip='1.1.1.1').status_code == 429
        assert _get(client, '/x', ip='9.9.9.9').status_code != 429


def test_static_and_healthz_not_counted(client):
    with patch.object(ratelimit, 'RATE_LIMIT', 1):
        for _ in range(3):
            assert _get(client, '/static/style.min.css').status_code != 429
            assert _get(client, '/healthz').status_code != 429


def test_local_direct_requests_exempt(client):
    with patch.object(ratelimit, 'RATE_LIMIT', 1):
        for _ in range(3):
            assert _get(client, '/x', ip='127.0.0.1').status_code != 429


def test_proxy_header_trusted_only_from_internal_address(client):
    with patch.object(ratelimit, 'RATE_LIMIT', 1):
        # Through a local proxy: each real client gets its own bucket.
        assert _get(client, '/x', ip='127.0.0.1', **{'X-Real-IP': '203.0.113.1'}).status_code != 429
        assert _get(client, '/x', ip='127.0.0.1', **{'X-Real-IP': '203.0.113.2'}).status_code != 429
        assert _get(client, '/x', ip='127.0.0.1', **{'X-Real-IP': '203.0.113.1'}).status_code == 429
        # Direct from the internet: a forged header doesn't buy a fresh bucket.
        _get(client, '/x', ip='8.8.8.8', **{'X-Real-IP': '10.0.0.1'})
        assert _get(client, '/x', ip='8.8.8.8', **{'X-Real-IP': '10.0.0.2'}).status_code == 429


def test_xff_uses_last_hop(client):
    with patch.object(ratelimit, 'RATE_LIMIT', 1):
        h = {'X-Forwarded-For': 'spoofed, 203.0.113.50'}
        _get(client, '/x', ip='127.0.0.1', **h)
        h2 = {'X-Forwarded-For': 'other-spoof, 203.0.113.50'}
        assert _get(client, '/x', ip='127.0.0.1', **h2).status_code == 429


def test_downloads_have_their_own_tighter_limit(client):
    with patch.object(ratelimit, 'RATE_LIMIT_DOWNLOADS', 1):
        _get(client, '/api/download')
        assert _get(client, '/api/download').status_code == 429
        assert _get(client, '/x').status_code != 429


def test_zero_disables(client):
    with patch.object(ratelimit, 'RATE_LIMIT', 0):
        for _ in range(5):
            assert _get(client, '/x').status_code != 429
