import gzip
import os

import brotli
import pytest

from app import app

BUNDLE = os.path.join(app.static_folder, 'app.bundle.js')


@pytest.fixture
def client():
    return app.test_client()


def _raw():
    with open(BUNDLE, 'rb') as f:
        return f.read()


def test_brotli_preferred_and_decodes_to_the_file(client):
    resp = client.get('/static/app.bundle.js?v=1', headers={'Accept-Encoding': 'gzip, br, zstd'})
    assert resp.status_code == 200
    assert resp.headers['Content-Encoding'] == 'br'
    assert 'Accept-Encoding' in resp.headers['Vary']
    assert int(resp.headers['Content-Length']) == len(resp.data)
    assert brotli.decompress(resp.data) == _raw()
    assert 'immutable' in resp.headers['Cache-Control']


def test_gzip_only_client_gets_gzip(client):
    resp = client.get('/static/app.bundle.js', headers={'Accept-Encoding': 'gzip'})
    assert resp.headers['Content-Encoding'] == 'gzip'
    assert gzip.decompress(resp.data) == _raw()


def test_identity_or_missing_encoding_gets_the_plain_file(client):
    for accept in ('identity', ''):
        resp = client.get('/static/app.bundle.js', headers={'Accept-Encoding': accept})
        assert 'Content-Encoding' not in resp.headers
        assert resp.data == _raw()


def test_compressed_etag_revalidates_to_304(client):
    first = client.get('/static/app.bundle.js', headers={'Accept-Encoding': 'br'})
    etag = first.headers['ETag']
    assert etag.endswith(':br"')
    again = client.get('/static/app.bundle.js',
                       headers={'Accept-Encoding': 'br', 'If-None-Match': etag})
    assert again.status_code == 304
    assert again.data == b''


def test_range_request_is_left_alone(client):
    resp = client.get('/static/app.bundle.js',
                      headers={'Accept-Encoding': 'br', 'Range': 'bytes=0-99'})
    assert resp.status_code == 206
    assert 'Content-Encoding' not in resp.headers
    assert resp.data == _raw()[:100]


def test_binary_files_untouched(client):
    resp = client.get('/static/apple-touch-icon.png', headers={'Accept-Encoding': 'br'})
    assert resp.status_code == 200
    assert 'Content-Encoding' not in resp.headers
