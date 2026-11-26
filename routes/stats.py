"""GET /stats (HTML, or JSON with ?format=json): the instance's operating numbers for its owner.
Requests served per route, upstream calls per host, cache hit rates, Reddit block events and
OAuth token state, with a per-minute history of the last hour (see core/stats.py).

Only answered for a request made on the server's own machine or from a browser holding the
owner cookie (reddit_login.is_local_request); everyone else gets a 404. Counters are per
process: under several gunicorn workers each page load shows whichever worker answered."""
import time
from datetime import datetime, timezone

from flask import Blueprint, abort, g, jsonify, render_template, request

from core import cache, stats
from core.config import RATE_LIMIT, RATE_LIMIT_DOWNLOADS
from reddit import login as reddit_login
from reddit import waf
from routes import health

bp = Blueprint("stats", __name__)

_SKIP_ENDPOINTS = {'static'}
_CHART_W, _CHART_H = 600, 120


def _start():
    g._stats_t0 = time.perf_counter()


def _record(resp):
    if request.endpoint in _SKIP_ENDPOINTS or request.path.startswith('/favicon'):
        return resp
    t0 = g.get('_stats_t0')
    route = request.url_rule.rule if request.url_rule else '(no route)'
    stats.record_request(route, resp.status_code, time.perf_counter() - t0 if t0 else None)
    return resp


def register(app):
    """Timing hooks. Called before ratelimit.register so the clock starts ahead of its check."""
    app.before_request(_start)
    app.after_request(_record)


def _require_owner():
    if not reddit_login.is_local_request():
        abort(404)


def _rate(hit, miss):
    total = hit + miss
    return round(100 * hit / total) if total else None


def _collect():
    snap = stats.snapshot()
    c = snap['counters']
    snap['caches'] = [
        {'name': 'Page responses', 'hits': c.get('view_cache_hit', 0), 'misses': c.get('view_cache_miss', 0),
         'size': len(cache._view_cache), 'note': '/api/* JSON, keyed by URL'},
        {'name': 'Fetch results', 'hits': c.get('fetch_cache_hit', 0) + c.get('fetch_cache_coalesced', 0),
         'misses': c.get('fetch_cache_miss', 0), 'size': len(cache._fetch_cache),
         'note': f"shared fetch_* calls; {c.get('fetch_cache_coalesced', 0)} joined one already in flight"},
    ]
    for row in snap['caches']:
        row['rate'] = _rate(row['hits'], row['misses'])
    snap['health'] = {'uptime_secs': round(time.time() - stats.STARTED), 'commit': health._COMMIT,
                      'oauth': health._oauth_state(), 'waf': waf.waf_status(), 'gql': health._gql_state(),
                      'cache': health._cache_state(),
                      'rate_limit': {'per_minute': RATE_LIMIT, 'downloads_per_minute': RATE_LIMIT_DOWNLOADS}}
    return snap


def _duration(secs):
    if secs is None:
        return '—'
    secs = int(secs)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f'{d}d {h}h'
    if h:
        return f'{h}h {m}m'
    return f'{m}m {s}s' if m else f'{s}s'


def _bars(history, total_key, error_key):
    """Geometry for one per-minute bar chart: a bar per minute, its errors stacked on top."""
    peak = max((m[total_key] for m in history), default=0)
    scale = (_CHART_H - 4) / peak if peak else 0
    step = _CHART_W / len(history)
    bars = []
    for i, m in enumerate(history):
        total, errors = m[total_key], m[error_key]
        ok_h = (total - errors) * scale
        err_h = errors * scale
        bars.append({'x': round(i * step + 1, 1), 'w': round(step - 2, 1),
                     'ok_y': round(_CHART_H - ok_h, 1), 'ok_h': round(ok_h, 1),
                     'err_y': round(_CHART_H - ok_h - err_h - (2 if errors and ok_h else 0), 1),
                     'err_h': round(err_h, 1), 'step': round(step, 1), 'slot_x': round(i * step, 1),
                     'title': f"{m['label']} UTC: {total} total, {errors} failed"})
    return {'bars': bars, 'peak': peak, 'w': _CHART_W, 'h': _CHART_H,
            'total': sum(m[total_key] for m in history), 'errors': sum(m[error_key] for m in history)}


def _refresh_secs():
    """Auto-reload interval from ?refresh= (0 turns it off), 30s by default."""
    raw = request.args.get('refresh', '30')
    return min(int(raw), 3600) if raw.isdigit() else 30


@bp.route("/stats")
def stats_page():
    # Not /stats.json: routes/passthrough.py claims every ".json" path for Reddit's API.
    _require_owner()
    data = _collect()
    if request.args.get('format') == 'json':
        resp = jsonify(data)
        resp.headers['Cache-Control'] = 'private, no-store'
        return resp
    h = data['history']
    for m in h:
        m['label'] = datetime.fromtimestamp(m['minute'], timezone.utc).strftime('%H:%M')
    html = render_template('stats.html', s=data, fmt_duration=_duration,
                           req_chart=_bars(h, 'requests', 'errors'),
                           up_chart=_bars(h, 'upstream', 'upstream_errors'),
                           since=datetime.fromtimestamp(data['since'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                           refresh=_refresh_secs())
    return html, 200, {'Cache-Control': 'private, no-store'}
