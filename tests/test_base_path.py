"""BASE_PATH (serving under a subpath behind a prefix-stripping proxy). The setting is read
at import time, so each check runs in a fresh interpreter."""
import json
import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(base, body):
    env = {**os.environ, 'BASE_PATH': base, 'REDDIT_OAUTH': '0'}
    out = subprocess.run([sys.executable, '-c', textwrap.dedent(body)], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_prefixed_app_path_and_html():
    r = _run('/rdvwr/', """
        import json, re
        from base_path import BASE_PATH, app_path
        from app import app
        c = app.test_client()
        html = c.get('/settings').get_data(as_text=True)
        idx = c.get('/').get_data(as_text=True)
        print(json.dumps(dict(base=BASE_PATH, a=app_path('/api/img?url=x'), dbl=app_path('//x'),
            settings_has_unprefixed=bool(re.search(r'(?:href|src|action)=.[/](?!rdvwr/)', html)),
            settings_has_prefixed='href="/rdvwr/' in html,
            meta='<meta name="rdvwr-base" content="/rdvwr">' in idx,
            static='/rdvwr/static/app.bundle.js' in idx,
            bare_static='"/static/' in idx)))
    """)
    assert r['base'] == '/rdvwr' and r['a'] == '/rdvwr/api/img?url=x' and r['dbl'] == '//x'
    assert r['settings_has_prefixed'] and not r['settings_has_unprefixed']
    assert r['meta'] and r['static'] and not r['bare_static']


def test_redirect_location_prefixed_once():
    r = _run('/rdvwr', """
        import json
        from app import app
        c = app.test_client()
        loc = c.get('/search?q=r/python').headers.get('Location')
        print(json.dumps(dict(loc=loc)))
    """)
    assert r['loc'].startswith('/rdvwr/r/python') and '/rdvwr/rdvwr' not in r['loc']


def test_unset_is_noop():
    r = _run('', """
        import json
        from base_path import BASE_PATH, app_path
        from app import app
        idx = app.test_client().get('/').get_data(as_text=True)
        print(json.dumps(dict(base=BASE_PATH, a=app_path('/api/x'), meta='content=""' in idx, static='"/static/app.bundle.js' in idx)))
    """)
    assert r == {'base': '', 'a': '/api/x', 'meta': True, 'static': True}
