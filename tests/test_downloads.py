"""Download redirects must never fetch a destination outside their own allowlist."""
import io
import zipfile
from unittest.mock import Mock

import pytest
import requests
from app import app
from routes import downloads


def response(status=200, body=b'fixture', **headers):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers)
    result._content = body
    result._content_consumed = True
    result.close = Mock()
    return result


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(downloads, 'DISABLE_DOWNLOADS', False)
    return app.test_client()


@pytest.mark.parametrize('destination', [
    'http://127.0.0.1/private', 'https://example.com/file', '//example.com/file',
    'file:///etc/passwd', 'https://i.redd.it:444/file', 'https://user@i.redd.it/file',
])
def test_redirect_destination_is_checked_before_fetch(monkeypatch, client, destination):
    redirect = response(302, Location=destination)
    get = Mock(return_value=redirect)
    monkeypatch.setattr(downloads.SESSION, 'get', get)
    result = client.get('/api/download?url=https://i.redd.it/start.jpg')
    assert result.status_code == 502
    assert get.call_count == 1
    assert get.call_args.kwargs['allow_redirects'] is False
    redirect.close.assert_called_once()


def test_relative_and_allowlisted_redirects_work_and_release_responses(monkeypatch, client):
    first = response(302, Location='/next.jpg')
    second = response(307, Location='https://preview.redd.it/final.jpg')
    final = response(**{'Content-Type': 'image/jpeg'})
    get = Mock(side_effect=[first, second, final])
    monkeypatch.setattr(downloads.SESSION, 'get', get)
    result = client.get('/api/download?url=https://i.redd.it/start.jpg')
    assert result.status_code == 200 and result.get_data() == b'fixture'
    result.close()
    assert [c.args[0] for c in get.call_args_list] == [
        'https://i.redd.it/start.jpg', 'https://i.redd.it/next.jpg', 'https://preview.redd.it/final.jpg',
    ]
    assert all(c.kwargs['allow_redirects'] is False for c in get.call_args_list)
    for upstream in (first, second, final):
        assert upstream.close.called


@pytest.mark.parametrize('location', ['', '/loop.jpg'])
def test_missing_location_and_redirect_loops_fail_closed(monkeypatch, client, location):
    redirect = response(302, Location=location)
    get = Mock(return_value=redirect)
    monkeypatch.setattr(downloads.SESSION, 'get', get)
    assert client.get('/api/download?url=https://i.redd.it/loop.jpg').status_code == 502
    assert get.call_count == (4 if location else 1)
    assert redirect.close.call_count == get.call_count


def test_stream_is_closed_when_download_is_abandoned(monkeypatch, client):
    upstream = response(body=b'x' * (downloads.STREAM_CHUNK_SIZE * 2))
    monkeypatch.setattr(downloads.SESSION, 'get', Mock(return_value=upstream))
    result = client.get('/api/download?url=https://i.redd.it/file.jpg', buffered=False)
    next(iter(result.response))
    result.close()
    assert upstream.close.called


def test_failed_upstream_is_closed(monkeypatch, client):
    upstream = response(404)
    monkeypatch.setattr(downloads.SESSION, 'get', Mock(return_value=upstream))
    assert client.get('/api/download?url=https://i.redd.it/file.jpg').status_code == 502
    upstream.close.assert_called_once()


@pytest.mark.parametrize('destination, expected_files', [
    ('/next.jpg', ['gallery-01.jpg']), ('https://i.imgur.com/file.jpg', []),
    ('http://127.0.0.1/private', []),
])
def test_gallery_uses_its_own_redirect_allowlist(monkeypatch, client, destination, expected_files):
    redirect, final = response(302, Location=destination), response()
    get = Mock(side_effect=[redirect, final])
    monkeypatch.setattr(downloads.SESSION, 'get', get)
    result = client.get('/api/download/gallery?urls=https://i.redd.it/start.jpg')
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.get_data())) as archive:
        assert archive.namelist() == expected_files
    assert get.call_count == (2 if expected_files else 1)
    assert redirect.close.called
    if expected_files:
        final.close.assert_called_once()
