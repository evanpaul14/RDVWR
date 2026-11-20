"""Open media proxy (PROXY_MEDIA=1): any public https host, with SSRF guards."""
import json
from unittest.mock import patch
import pytest
from app import app
from core.safe_fetch import is_public_hostname, is_public_ip
from routes import mediaproxy
from tests.test_routes import MockResponse


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def on():
    app.config['PROXY_MEDIA'] = True
    yield
    app.config['PROXY_MEDIA'] = False


@pytest.mark.parametrize('addr', ['127.0.0.1', '10.0.0.5', '192.168.1.20', '169.254.169.254', '::1',
                                  'fe80::1', '::ffff:10.0.0.1', '100.64.0.1', '0.0.0.0'])
def test_private_addresses_blocked(addr):
    assert not is_public_ip(addr)


def test_public_address_allowed():
    assert is_public_ip('93.184.216.34') and is_public_ip('2606:4700::1111')


@pytest.mark.parametrize('host', ['localhost', '10.0.0.1', '1.2.3.4', 'printer.local', 'nas.lan', 'foo', 'a..b.com'])
def test_non_public_hostnames_rejected(host):
    assert not is_public_hostname(host)


def test_open_host_forbidden_when_off(client):
    assert client.get('/api/m/example.com/x.jpg').status_code == 403


def test_open_host_streams_image(client, on):
    with patch.object(mediaproxy._OPEN_SESSION, 'get') as g:
        g.return_value = MockResponse(status_code=200, headers={'Content-Type': 'image/gif'}, raw_bytes=b'GIF')
        r = client.get('/api/m/media2.giphy.com/media/a/giphy.gif')
    assert r.status_code == 200 and r.data == b'GIF'
    assert r.headers['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.parametrize('ct', ['text/html', 'image/svg+xml', 'application/javascript'])
def test_open_host_rejects_non_media(client, on, ct):
    with patch.object(mediaproxy._OPEN_SESSION, 'get') as g:
        g.return_value = MockResponse(status_code=200, headers={'Content-Type': ct}, raw_bytes=b'x')
        assert client.get('/api/m/example.com/x').status_code == 415


def test_open_host_size_cap(client, on):
    with patch.object(mediaproxy._OPEN_SESSION, 'get') as g:
        g.return_value = MockResponse(status_code=200, raw_bytes=b'x',
                                      headers={'Content-Type': 'video/mp4', 'Content-Length': str(10**10)})
        assert client.get('/api/m/example.com/x.mp4').status_code == 413


def test_redirect_to_ip_literal_not_followed(client, on):
    with patch.object(mediaproxy._OPEN_SESSION, 'get') as g:
        g.return_value = MockResponse(status_code=302, headers={'Location': 'https://169.254.169.254/x'})
        assert client.get('/api/m/example.com/x.jpg').status_code == 502
        assert g.call_count == 1


def test_own_host_not_proxied(client, on):
    assert client.get('/api/m/localhost/x.jpg', base_url='http://localhost').status_code == 403


def test_json_media_keys_rewritten_but_links_not(client, on):
    with app.test_request_context('/api/r/x'):
        body = json.dumps({'url': 'https://news.example.com/story', 'gif_url': 'https://cdn.example.org/a.gif',
                           'preview_img': 'https://x.example.net/p.jpg',
                           'html': '<img src="https://pics.example.com/a.png"><iframe src="https://yt.example.com/e">'}).encode()
        from flask import make_response
        resp = make_response(body, 200, {'Content-Type': 'application/json'})
        out = json.loads(mediaproxy.rewrite_media_urls(resp).get_data())
    assert out['url'] == 'https://news.example.com/story'
    assert out['gif_url'] == '/api/m/cdn.example.org/a.gif'
    assert out['preview_img'] == '/api/m/x.example.net/p.jpg'
    assert '<img src="/api/m/pics.example.com/a.png">' in out['html']
    assert 'src="https://yt.example.com/e"' in out['html']
