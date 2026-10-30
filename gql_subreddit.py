"""A subreddit's about info and rules from the Reddit Android app's GraphQL endpoint, for when
Reddit's WAF blocks the REST about.json / rules.json (see gql_posts.py for the feeds).
Results are the dicts routes/subreddit.fetch_about and fetch_rules build."""
import html as html_lib
from gql_client import gql_query
from gql_richtext import richtext_html
from helpers import UpstreamError
from media_detection import clean_url
from reddit_html import clean_reddit_html

# UnavailableSubreddit.forbiddenReason -> (state, message, status), matching
# routes/subreddit._SUBREDDIT_ERROR_MESSAGES. Anything else that isn't public is "private".
_UNAVAILABLE = {
    "BANNED":      ("banned", "This subreddit has been banned", 404),
    "QUARANTINED": ("quarantined", "This subreddit is quarantined", 403),
}


def _unavailable(node, fallback_message="This subreddit is private"):
    state, message, status = _UNAVAILABLE.get(
        node.get("forbiddenReason"), ("private", fallback_message, 403))
    return UpstreamError(message, status, state)


def fetch_gql_about(subreddit, timeout=15):
    """The dict fetch_about returns. Raises UpstreamError for missing / banned / private
    subreddits. The app has no "online now" count, so `active` is 0 (the UI hides it)."""
    data = gql_query("SubredditInfoByName", {"subredditName": subreddit, "includeRecapFields": False,
                                             "includeDevvitData": False}, timeout)
    node = (data.get("data") or {}).get("subredditInfoByName")
    if not node:
        raise UpstreamError("Subreddit not found", 404, "not_found")
    if node.get("__typename") == "UnavailableSubreddit":
        raise _unavailable(node)
    styles = node.get("styles") or {}
    description = node.get("description") or {}
    sub_type = (node.get("type") or "public").lower()
    state = "quarantined" if node.get("isQuarantined") else (sub_type if sub_type != "public" else None)
    return {
        "title":        node.get("title") or subreddit,
        "description":  node.get("publicDescriptionText") or "",
        "sidebar":      description.get("markdown") or "",
        "sidebar_html": clean_reddit_html(html_lib.escape(richtext_html(description.get("richtext")))),
        "subscribers":  int(node.get("subscribersCount") or 0),
        "user_is_subscriber": node.get("isSubscribed"),
        "active":       0,
        "icon":         clean_url((styles.get("legacyIcon") or {}).get("url") or styles.get("icon") or ""),
        "state":        state,
    }


def fetch_gql_rules(subreddit, timeout=15):
    """[{"short_name", "description"}] like fetch_rules; [] when the subreddit has none."""
    data = gql_query("GetRulesBySubredditName", {"subredditName": subreddit}, timeout)
    node = (data.get("data") or {}).get("subredditInfoByName") or {}
    return [{"short_name": r.get("name", ""), "description": (r.get("content") or {}).get("markdown", "")}
            for r in node.get("rules") or []]
