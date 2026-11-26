"""In-process operating counters for the /stats page: requests served, upstream calls,
cache hits and Reddit block events, plus a per-minute history of the last hour.

Counters are per process (each gunicorn worker keeps its own) and reset on restart; nothing
here identifies a visitor: no IPs, paths with query strings or user agents, only route
templates (e.g. /api/r/<sub>) and upstream hostnames. Imports nothing from the project, so
any module can record into it."""
import threading
import time
from collections import Counter, deque

HISTORY_MINUTES = 60
_LATENCY_SAMPLES = 500  # most recent durations kept per series, for the median/p95

_lock = threading.Lock()
STARTED = time.time()


class _Series:
    """Count, status mix and recent latencies for one route or upstream host."""
    __slots__ = ('count', 'errors', 'statuses', 'durations')

    def __init__(self):
        self.count = 0
        self.errors = 0
        self.statuses = Counter()
        self.durations = deque(maxlen=_LATENCY_SAMPLES)


_routes = {}       # route template -> _Series
_upstream = {}     # upstream host -> _Series
_counters = Counter()
# minute (epoch // 60) -> Counter of 'requests', 'errors', 'upstream', 'upstream_errors'
_minutes = {}


def _minute_bucket(now):
    minute = int(now // 60)
    bucket = _minutes.get(minute)
    if bucket is None:
        bucket = _minutes[minute] = Counter()
        for old in [m for m in _minutes if m <= minute - HISTORY_MINUTES]:
            del _minutes[old]
    return bucket


def incr(name, n=1):
    """Bump a named counter (cache hits, WAF blocks, ...)."""
    with _lock:
        _counters[name] += n


def record_request(route, status, seconds=None):
    """`seconds` is None when the request was answered before the timer started."""
    error = status >= 500
    with _lock:
        s = _routes.get(route)
        if s is None:
            s = _routes[route] = _Series()
        s.count += 1
        s.errors += error
        s.statuses[status // 100] += 1
        if seconds is not None:
            s.durations.append(seconds)
        bucket = _minute_bucket(time.time())
        bucket['requests'] += 1
        bucket['errors'] += error


def record_upstream(host, status, seconds):
    """`status` is the HTTP status, or None when the call raised (timeout, TLS, DNS)."""
    error = status is None or status >= 400
    with _lock:
        s = _upstream.get(host)
        if s is None:
            s = _upstream[host] = _Series()
        s.count += 1
        s.errors += error
        s.statuses['failed' if status is None else status // 100] += 1
        s.durations.append(seconds)
        bucket = _minute_bucket(time.time())
        bucket['upstream'] += 1
        bucket['upstream_errors'] += error


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(p * len(sorted_vals)))]


def _series_view(name, s):
    d = sorted(s.durations)
    return {'name': name, 'count': s.count, 'errors': s.errors,
            'statuses': {str(k) + ('' if k == 'failed' else 'xx'): v for k, v in sorted(s.statuses.items(), key=str)},
            'p50_ms': round(_pct(d, .5) * 1000) if d else None,
            'p95_ms': round(_pct(d, .95) * 1000) if d else None}


def snapshot():
    """A JSON-able copy of everything, routes and hosts sorted busiest first."""
    now = time.time()
    with _lock:
        routes = sorted((_series_view(k, v) for k, v in _routes.items()), key=lambda r: -r['count'])
        upstream = sorted((_series_view(k, v) for k, v in _upstream.items()), key=lambda r: -r['count'])
        counters = dict(_counters)
        current = int(now // 60)
        history = []
        for m in range(current - HISTORY_MINUTES + 1, current + 1):
            b = _minutes.get(m, {})
            history.append({'minute': m * 60, 'requests': b.get('requests', 0), 'errors': b.get('errors', 0),
                            'upstream': b.get('upstream', 0), 'upstream_errors': b.get('upstream_errors', 0)})
    return {'since': STARTED, 'routes': routes, 'upstream': upstream, 'counters': counters, 'history': history}


def reset():
    """Clear everything (tests)."""
    with _lock:
        _routes.clear()
        _upstream.clear()
        _counters.clear()
        _minutes.clear()
