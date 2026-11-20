import pytest

from core.helpers import checked_url_host


@pytest.mark.parametrize("url,host", [
    ("https://i.redd.it/x.jpg", "i.redd.it"),
    ("http://Example.COM:8080/a?b=1#c", "example.com"),
    ("https://[2606:4700::1]/", "2606:4700::1"),
    ("https://www.reddit.com/r/x/s/abc", "www.reddit.com"),
])
def test_plain_urls(url, host):
    assert checked_url_host(url) == host


@pytest.mark.parametrize("url", [
    "http://127.0.0.1\\@i.redd.it/",        # urlparse/urllib3 parser differential
    "http://127.0.0.1:1\\@i.redd.it/",
    "https://i.redd.it\\@127.0.0.1/",
    "https://user@i.redd.it/",
    "https://user:pw@i.redd.it/",
    "https://evil.com@i.redd.it/",
    "https://@i.redd.it/",
    "https://i.redd.it /x",
    "https://i.redd.it\n/x",
    "https://i.redd.it:99999/",
    "https://i.redd.it:abc/",
    "ftp://i.redd.it/",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "//i.redd.it/x",
    "https:///x",
    "",
    None,
])
def test_rejected(url):
    assert checked_url_host(url) is None


def test_scheme_restriction():
    assert checked_url_host("http://i.redd.it/", schemes=("https",)) is None
