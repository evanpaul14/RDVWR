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
