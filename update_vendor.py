#!/usr/bin/env python3
"""Download/update vendored JS libraries to static/. Run manually when updates are needed."""
import sys
from pathlib import Path
import requests

STATIC = Path(__file__).parent / 'static'

LIBS = [
    {'pkg': 'marked',    'major': 12, 'file': 'marked.min.js',     'cdn_path': 'marked.min.js'},
    {'pkg': 'dompurify', 'major': 3,  'file': 'purify.min.js',     'cdn_path': 'dist/purify.min.js'},
    {'pkg': 'hls.js',    'major': 1,  'file': 'hls.min.js',        'cdn_path': 'dist/hls.min.js'},
]

def latest_in_major(pkg, major):
    data = requests.get(f'https://registry.npmjs.org/{pkg}', timeout=15).json()
    candidates = [v for v in data['versions'] if v.startswith(f'{major}.') and '-' not in v]
    if not candidates:
        raise RuntimeError(f'No versions found for {pkg}@{major}.x')
    return sorted(candidates, key=lambda v: [int(x) for x in v.split('-')[0].split('.')])[-1]

ok = True
for lib in LIBS:
    try:
        ver = latest_in_major(lib['pkg'], lib['major'])
        url = f"https://cdn.jsdelivr.net/npm/{lib['pkg']}@{ver}/{lib['cdn_path']}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        out = STATIC / lib['file']
        out.write_text(resp.text, encoding='utf-8')
        print(f"  {lib['pkg']}@{ver}  →  static/{lib['file']}")
    except Exception as e:
        print(f"  ERROR {lib['pkg']}: {e}", file=sys.stderr)
        ok = False

sys.exit(0 if ok else 1)
