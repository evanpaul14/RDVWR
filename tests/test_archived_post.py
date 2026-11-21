"""A post Reddit no longer returns is recovered from the Arctic Shift archive."""
from unittest.mock import MagicMock, patch

import pytest

from core.helpers import UpstreamError
from reddit import archive
from routes import comments as comments_routes

RAW = {"id": "abc123", "name": "t3_abc123", "title": "Gone", "author": "someone", "subreddit": "x",
       "permalink": "/r/x/comments/abc123/gone/", "created_utc": 1.0, "is_self": True, "selftext": "orig"}


def _resp(data):
    r = MagicMock()
    r.json.return_value = {"data": data}
    return r


def test_fetch_archived_post_flags_it():
    with patch.object(archive, "arctic_shift_get", return_value=_resp([RAW])):
        post = archive.fetch_archived_post("abc123")
    assert post["_archived"] is True and post["title"] == "Gone"


@pytest.mark.parametrize("data", [[], [{"id": "other"}]])
def test_fetch_archived_post_missing_is_404(data):
    with patch.object(archive, "arctic_shift_get", return_value=_resp(data)):
        with pytest.raises(UpstreamError) as e:
            archive.fetch_archived_post("abc123")
    assert e.value.status == 404


def test_archive_failure_is_404():
    with patch.object(archive, "arctic_shift_get", side_effect=RuntimeError("down")):
        with pytest.raises(UpstreamError) as e:
            archive.fetch_archived_post("abc123")
    assert e.value.status == 404


def test_by_id_post_falls_back_to_archive_on_404():
    with patch.object(comments_routes, "_reddit_post", side_effect=UpstreamError("Post not found", 404)), \
         patch.object(comments_routes, "fetch_archived_post", return_value={**RAW, "_archived": True}):
        assert comments_routes._by_id_post("abc123")["_archived"] is True


def test_by_id_post_keeps_the_original_404_when_archive_has_nothing():
    with patch.object(comments_routes, "_reddit_post", side_effect=UpstreamError("Post not found", 404)), \
         patch.object(comments_routes, "fetch_archived_post", side_effect=UpstreamError("nope", 404)):
        with pytest.raises(UpstreamError) as e:
            comments_routes._by_id_post("abc123")
    assert e.value.message == "Post not found"


def test_other_errors_do_not_reach_the_archive():
    """A private or blocked subreddit must not be served from the archive."""
    with patch.object(comments_routes, "_reddit_post", side_effect=UpstreamError("private", 403)), \
         patch.object(comments_routes, "fetch_archived_post") as arch:
        with pytest.raises(UpstreamError):
            comments_routes._by_id_post("abc123")
    arch.assert_not_called()


def test_stub_post_gets_author_and_body_from_archive():
    stub = {**RAW, "author": "[deleted]", "selftext": "[deleted]", "selftext_html": None, "score": 7}
    with patch.object(comments_routes, "fetch_archived_post", return_value={**RAW, "_archived": True}):
        out = comments_routes._restore_deleted_author(stub)
    assert out["author"] == "someone" and out["selftext"] == "orig" and out["_archived"] and out["score"] == 7


def test_stub_stays_when_archive_also_lacks_the_author():
    stub = {**RAW, "author": "[deleted]", "selftext": "[deleted]"}
    with patch.object(comments_routes, "fetch_archived_post", return_value={**RAW, "author": "[deleted]"}):
        assert comments_routes._restore_deleted_author(stub) is stub
    with patch.object(comments_routes, "fetch_archived_post", side_effect=UpstreamError("x", 404)):
        assert comments_routes._restore_deleted_author(stub) is stub


def test_live_posts_never_touch_the_archive():
    with patch.object(comments_routes, "fetch_archived_post") as arch:
        assert comments_routes._restore_deleted_author(RAW) is RAW
    arch.assert_not_called()


def test_title_only_post_gets_an_empty_body_not_deleted():
    stub = {**RAW, "author": "[deleted]", "selftext": "[deleted]"}
    with patch.object(comments_routes, "fetch_archived_post", return_value={**RAW, "selftext": ""}):
        assert comments_routes._restore_deleted_author(stub)["selftext"] == ""
