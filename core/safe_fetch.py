"""HTTP session for fetching arbitrary third-party URLs (the open media proxy).

Refuses to talk to anything that isn't a public internet address. The check runs on the
address of the *connected socket*, so it covers DNS rebinding and every redirect hop
(unlike resolving the hostname up front and trusting a second lookup to agree).
"""
import ipaddress
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool

# A real DNS name: no IP literals (the last label must start with a letter), no port.
_HOSTNAME_RE = re.compile(r'^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}$', re.I)
_LOCAL_SUFFIXES = ('.local', '.localhost', '.internal', '.lan', '.home', '.localdomain', '.intranet')


class BlockedAddress(OSError):
    pass


def is_public_hostname(host):
    """Syntactic check only — the connection guard below is what enforces it."""
    host = (host or '').lower()
    return bool(_HOSTNAME_RE.match(host)) and not host.endswith(_LOCAL_SUFFIXES)


def is_public_ip(addr):
    try:
        ip = ipaddress.ip_address(addr.split('%')[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class _GuardedConnection(HTTPSConnection):
    def _new_conn(self):
        sock = super()._new_conn()
        try:
            addr = sock.getpeername()[0]
        except OSError:
            sock.close()
            raise
        if not is_public_ip(addr):
            sock.close()
            raise BlockedAddress(f'refusing non-public address {addr}')
        return sock


class _GuardedPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedConnection


class _GuardedAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {'https': _GuardedPool}


def build_session(headers=None):
    s = requests.Session()
    s.trust_env = False  # no proxy/netrc env: the guard must see the real peer
    if headers:
        s.headers.update(headers)
    s.mount('https://', _GuardedAdapter())
    s.mount('http://', HTTPAdapter(max_retries=0))  # plain http is never requested
    return s
