"""Regression tests for upstream budgets and fetch coordination (no network)."""
from unittest.mock import Mock, patch
import pytest
import requests
from upstream_budget import budget, request, backoff
from routes.subreddit import fetch_feed
import helpers


def test_nested_requests_share_remaining_budget():
    clock = [100.0]
    call = Mock()
    with patch('upstream_budget.time.monotonic', side_effect=lambda: clock[0]):
        with budget(6):
            clock[0] += 4
            with budget(15):
                request(call, 'url', timeout=15)
                assert call.call_args.kwargs['timeout'] == 2
                clock[0] += 2
                with pytest.raises(requests.exceptions.Timeout):
                    request(call, 'url', timeout=15)
        request(call, 'url', timeout=15)
        assert call.call_args.kwargs['timeout'] == 15


def test_backoff_does_not_exceed_deadline():
    with patch('upstream_budget.time.sleep') as sleep:
        with budget(1):
            with pytest.raises(requests.exceptions.Timeout):
                backoff(5)
        sleep.assert_not_called()


def test_feed_fallback_uses_rest_budget():
    helpers._fetch_cache.clear()
    clock = [100.0]
    def rest(*args, **kwargs):
        clock[0] += 4
        return Mock(status_code=403, headers={'content-type': 'text/html'})
    def fallback(*args, **kwargs):
        call = Mock()
        request(call, 'gql', timeout=15)
        assert call.call_args.kwargs['timeout'] == 2
        return {'posts': [], 'after': None}
    with patch('upstream_budget.time.monotonic', side_effect=lambda: clock[0]), \
         patch('helpers.reddit_login.request_account_active', return_value=False), \
         patch('routes.subreddit.reddit_get', side_effect=rest), \
         patch('routes.subreddit.fetch_gql_feed', side_effect=fallback):
        fetch_feed('budgettest', 'hot', timeout=6)


@pytest.mark.parametrize('kind', ['home', 'multi', 'search'])
def test_listing_hydration_matches_server_request(kind):
    from app import app
    from routes import page_data
    payload = {'posts': [], 'after': 'next', 'title': 'List'}
    fetch = {'home': 'fetch_frontpage', 'multi': 'fetch_multireddit', 'search': 'fetch_search_posts'}[kind]
    with app.test_request_context('/'), patch.object(page_data, fetch, return_value=payload), \
         patch('reddit_login.request_account_active', return_value=False):
        if kind == 'home':
            result = page_data.build_home('hot', 'all', 'cursor')
        elif kind == 'multi':
            result = page_data.build_multi('User', 'List', 'hot', 'all', 'cursor')
        else:
            result = page_data.build_search('cats', after='cursor')
        initial = result['initial_listing']
        assert initial['kind'] == kind
        assert initial['params']['after'] == 'cursor'
        assert initial['data'] == payload
        assert initial['account'] is None


def test_fetch_cache_normalizes_defaults_and_copies_results():
    helpers._fetch_cache.clear()
    calls = []
    @helpers.fetch_cache(60)
    def fetch(value, optional='', timeout=10):
        calls.append(value)
        return {'items': [value]}
    with patch('helpers.reddit_login.request_account_active', return_value=False):
        fetch('same')['items'].clear()
        assert fetch(value='same', timeout=6)['items'] == ['same']
        assert fetch('same', '')['items'] == ['same']
    assert calls == ['same']


@pytest.mark.parametrize('fails', [False, True])
def test_fetch_cache_coalesces_concurrent_results_and_failures(fails):
    from concurrent.futures import ThreadPoolExecutor, Future
    from threading import Event, Barrier, Lock
    helpers._fetch_cache.clear()
    entered, release = Event(), Event()
    followers = Barrier(4)
    calls = []
    @helpers.fetch_cache(60)
    def fetch(value, timeout=10):
        calls.append(value)
        entered.set()
        assert release.wait(3)
        if fails:
            raise ValueError('upstream failed')
        return {'items': [value]}
    original_result = Future.result
    # Observe three followers reaching the shared Future before releasing the leader.
    def result(future, timeout=None):
        if future in helpers._fetch_inflight.values():
            followers.wait(timeout=3)
        return original_result(future, timeout)
    with patch('helpers.reddit_login.request_account_active', return_value=False), \
         patch.object(Future, 'result', result), ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(fetch, 'same')]
        assert entered.wait(3)
        jobs += [pool.submit(fetch, 'same') for _ in range(3)]
        followers.wait(timeout=3)
        release.set()
        if fails:
            for job in jobs:
                with pytest.raises(ValueError, match='upstream failed'):
                    job.result()
        else:
            values = [job.result() for job in jobs]
            values[0]['items'].clear()
            assert all(value['items'] == ['same'] for value in values[1:])
    assert calls == ['same']
    assert not helpers._fetch_inflight
    if fails:
        with patch('helpers.reddit_login.request_account_active', return_value=False), pytest.raises(ValueError):
            fetch('same')
        assert calls == ['same', 'same']


def test_fetch_cache_never_shares_account_reads():
    calls = []
    @helpers.fetch_cache(60)
    def fetch(value):
        calls.append(value)
        return value
    with patch('helpers.reddit_login.request_account_active', return_value=True):
        fetch('private')
        fetch('private')
    assert calls == ['private', 'private']
