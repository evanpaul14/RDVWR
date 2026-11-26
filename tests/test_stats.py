"""The /stats page and the counters behind it (core/stats.py)."""
from unittest.mock import MagicMock

import pytest

from app import app
from core import cache, ratelimit, stats
from reddit import budget, waf
from reddit import login as reddit_login
from reddit import owner as reddit_owner

PROXIED = {'X-Forwarded-For': '1.2.3.4', 'X-Real-IP': '192.168.1.5'}


@pytest.fixture(autouse=True)
def _fresh():
    stats.reset()
    ratelimit.reset()
    yield
    stats.reset()


def test_page_and_json_answer_local_requests():
    c = app.test_client()
    r = c.get('/stats')
    assert r.status_code == 200 and b'rdvwr' in r.data and b'Last 60 minutes' in r.data
    assert r.headers['Cache-Control'] == 'private, no-store'
    d = c.get('/stats?format=json').get_json()
    assert set(d) == {'since', 'routes', 'upstream', 'counters', 'history', 'caches', 'health'}
    assert len(d['history']) == stats.HISTORY_MINUTES


@pytest.mark.parametrize('kwargs', [{'headers': PROXIED}, {'base_url': 'http://rdvwr.example.com'}])
def test_hidden_from_everyone_else(kwargs):
    assert app.test_client().get('/stats', **kwargs).status_code == 404
    assert app.test_client().get('/stats?format=json', **kwargs).status_code == 404


def test_owner_cookie_opens_it_through_a_proxy(monkeypatch):
    monkeypatch.setattr(reddit_login, 'ENABLED', True)
    monkeypatch.setattr(reddit_owner, 'KEY', 'k' * 32)
    c = app.test_client()
    c.set_cookie(reddit_owner.COOKIE, reddit_owner.make_token())
    assert c.get('/stats', headers=PROXIED).status_code == 200


def test_requests_are_counted_by_route_template_not_path():
    c = app.test_client()
    c.get('/healthz')
    c.get('/healthz')
    c.get('/static/favicon.svg')
    routes = {r['name']: r for r in stats.snapshot()['routes']}
    assert routes['/healthz']['count'] == 2 and routes['/healthz']['statuses'] == {'2xx': 2}
    assert routes['/healthz']['p50_ms'] is not None
    assert not any(name.startswith('/static') for name in routes)
    assert stats.snapshot()['history'][-1]['requests'] == 2


def test_rate_limited_requests_still_count(monkeypatch):
    monkeypatch.setattr('core.ratelimit.RATE_LIMIT', 1)
    c = app.test_client()
    c.get('/settings', headers=PROXIED)
    assert c.get('/settings', headers=PROXIED).status_code == 429
    snap = stats.snapshot()
    assert snap['counters']['rate_limited'] == 1
    assert {r['name']: r for r in snap['routes']}['/settings']['statuses'] == {'2xx': 1, '4xx': 1}


def test_upstream_calls_record_host_status_and_failures():
    budget.request(MagicMock(return_value=MagicMock(status_code=200)), 'https://oauth.reddit.com/r/pics.json')
    budget.request(MagicMock(return_value=MagicMock(status_code=503)), 'https://oauth.reddit.com/r/x.json')
    with pytest.raises(TimeoutError):
        budget.request(MagicMock(side_effect=TimeoutError), 'https://arctic-shift.photon-reddit.com/api/x')
    up = {r['name']: r for r in stats.snapshot()['upstream']}
    assert up['oauth.reddit.com']['count'] == 2 and up['oauth.reddit.com']['errors'] == 1
    assert up['oauth.reddit.com']['statuses'] == {'2xx': 1, '5xx': 1}
    assert up['arctic-shift.photon-reddit.com']['statuses'] == {'failed': 1}
    assert stats.snapshot()['history'][-1]['upstream_errors'] == 2


def test_fetch_cache_hits_and_misses_are_counted():
    calls = []

    @cache.fetch_cache(60)
    def fetch_thing(x, timeout=10):
        calls.append(x)
        return {'x': x}

    with app.test_request_context('/'):
        fetch_thing('stats-test-a')
        fetch_thing('stats-test-a')
    c = stats.snapshot()['counters']
    assert calls == ['stats-test-a']
    assert c['fetch_cache_miss'] == 1 and c['fetch_cache_hit'] == 1


def test_waf_events_are_counted():
    for _ in range(waf._WAF_BURST_COUNT):
        waf.note_waf_block()
    waf.waf_blocked_response()
    c = stats.snapshot()['counters']
    assert c['waf_block'] == waf._WAF_BURST_COUNT and c['waf_cooldown'] == 1 and c['waf_skipped'] == 1
    assert b'Reddit blocking' in app.test_client().get('/stats').data


def test_history_drops_minutes_older_than_an_hour(monkeypatch):
    now = 1_000_000_000
    monkeypatch.setattr(stats.time, 'time', lambda: now)
    stats.record_upstream('a', 200, 0.1)
    now += (stats.HISTORY_MINUTES + 5) * 60
    stats.record_upstream('a', 200, 0.1)
    assert len(stats._minutes) == 1
    hist = stats.snapshot()['history']
    assert sum(m['upstream'] for m in hist) == 1 and hist[-1]['upstream'] == 1


@pytest.mark.parametrize('arg,expect', [('', b'http-equiv="refresh" content="30"'),
                                        ('?refresh=0', b'auto-reload</a>'),
                                        ('?refresh=x" onload', b'content="30"')])
def test_refresh_param(arg, expect):
    assert expect in app.test_client().get('/stats' + arg).data
