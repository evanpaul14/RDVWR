"""POST /api/translate: validation, same-site check, chunked upstream calls, caching."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from unittest.mock import patch, MagicMock
from app import app
from routes import translate as tr


@pytest.fixture
def client():
    app.config["TESTING"] = True
    tr._cache.clear()
    with app.test_client() as c:
        c.environ_base["HTTP_SEC_FETCH_SITE"] = "same-origin"
        yield c


def _resp(trans, src="fr"):
    m = MagicMock()
    m.json.return_value = {"sentences": [{"trans": trans}, {"orig": "x"}], "src": src}
    return m


def test_translates_and_caches(client):
    with patch.object(tr.cffi_requests, "post", return_value=_resp("Hello")) as post:
        for _ in range(2):
            r = client.post("/api/translate", json={"texts": ["Bonjour"], "to": "en"})
            assert r.status_code == 200
            assert r.get_json() == {"texts": ["Hello"], "source": "fr"}
        assert post.call_count == 1


def test_rejects_bad_input(client):
    assert client.post("/api/translate", json={"texts": [], "to": "en"}).status_code == 400
    assert client.post("/api/translate", json={"texts": ["a"], "to": "e n!"}).status_code == 400
    assert client.post("/api/translate", json={"texts": ["a" * 10001], "to": "en"}).status_code == 400


def test_cross_site_forbidden(client):
    r = client.post("/api/translate", json={"texts": ["a"]}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_long_text_is_chunked(client):
    text = "\n".join(f"word{i} " * 70 for i in range(12))
    with patch.object(tr.cffi_requests, "post", return_value=_resp("x")) as post:
        assert client.post("/api/translate", json={"texts": [text], "to": "en"}).status_code == 200
        assert post.call_count > 1


def test_upstream_failure_is_502(client):
    with patch.object(tr.cffi_requests, "post", side_effect=tr.cffi_requests.exceptions.ConnectionError("down")):
        assert client.post("/api/translate", json={"texts": ["a"], "to": "en"}).status_code == 502
