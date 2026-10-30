import pytest
from unittest.mock import patch

# Every GraphQL fallback the routes import. They run when a test mocks Reddit's block page,
# and must not reach the real API. test_gql_feed.py patches them itself where it needs them.
GQL_FALLBACKS = (
    "routes.subreddit.fetch_gql_feed", "routes.subreddit.fetch_gql_about", "routes.subreddit.fetch_gql_rules",
    "routes.home.fetch_gql_frontpage", "routes.users.fetch_gql_user_posts",
    "routes.search.fetch_gql_search_posts", "routes.search.fetch_gql_search_users",
    "routes.search.fetch_gql_search_communities",
)


@pytest.fixture(autouse=True)
def _no_gql_network():
    patches = [patch(name, side_effect=RuntimeError("GraphQL disabled in tests")) for name in GQL_FALLBACKS]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()
