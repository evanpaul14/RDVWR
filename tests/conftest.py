import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _no_gql_feed_network():
    """fetch_feed falls back to Reddit's GraphQL API when REST is blocked; tests that mock a
    blocked REST response must not reach the real one. test_gql_feed.py patches it itself."""
    with patch("routes.subreddit.fetch_gql_feed", side_effect=RuntimeError("GraphQL disabled in tests")):
        yield
