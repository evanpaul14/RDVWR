"""URL safety checks: same-site redirect targets and the host an outbound fetch would hit."""
import re
from urllib.parse import urlsplit

import idna
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url


def safe_next(raw):
    """Only ever redirect back to a same-site path."""
    if not raw or not raw.startswith('/') or raw.startswith('//') or '\\' in raw:
        return '/'
    return raw


_URL_BAD_CHARS_RE = re.compile(r'[\x00-\x20\x7f\\]')


def _idna_host(host):
    """`host` as urllib3 sends it: lowercase, non-ASCII names punycoded (same call as urllib3's
    _idna_encode). Raises ValueError for a name IDNA can't encode."""
    host = host.lower()
    if host.isascii():
        return host
    try:
        return idna.encode(host, strict=True, std3_rules=True).decode('ascii')
    except idna.IDNAError as e:
        raise ValueError(str(e)) from e


def checked_url_host(url, schemes=('http', 'https')):
    """The lowercase ASCII host an outbound fetch of `url` would actually connect to
    (punycode for an internationalized name), or None if the URL is malformed or ambiguous.
    Use this, not urlparse().hostname, before checking a host against an allowlist: urlparse
    and requests (urllib3) can disagree about the host, e.g. "http://127.0.0.1\\@i.redd.it/"
    is i.redd.it to urlparse but 127.0.0.1 to urllib3. Rejects backslashes, whitespace/control
    chars and userinfo, then requires both parsers to agree on the host."""
    if not isinstance(url, str) or not url or _URL_BAD_CHARS_RE.search(url):
        return None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        parsed.port   # raises ValueError on a malformed port
        u3_host = parse_url(url).host
        host = host and _idna_host(host)
    except (ValueError, LocationParseError):
        return None
    if parsed.scheme not in schemes or not host or '@' in parsed.netloc \
            or parsed.username is not None or parsed.password is not None:
        return None
    if not u3_host or u3_host.strip('[]').lower() != host:
        return None
    return host
