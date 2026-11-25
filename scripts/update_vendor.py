#!/usr/bin/env python3
"""Download/update vendored JS libraries to static/.

Updates each library to the newest release within its pinned major version and records
the installed versions in static/vendor.json. A newer major is only reported, never
installed: raise 'major' below by hand after checking the library's changelog.

Writes a Markdown summary of what changed to the path given by --summary (used as the
body of the update PR). Exit status is 1 if any download failed.
"""
import argparse
import json
import sys
from pathlib import Path

import requests

STATIC = Path(__file__).parent.parent / 'static'
MANIFEST = STATIC / 'vendor.json'

LIBS = [
    {'pkg': 'marked',    'major': 12, 'file': 'marked.min.js',     'cdn_path': 'marked.min.js'},
    {'pkg': 'dompurify', 'major': 3,  'file': 'purify.min.js',     'cdn_path': 'dist/purify.min.js'},
    # Regular (non-'light') build — Reddit's videos need alt-audio-track support,
    # which the light build strips out entirely.
    {'pkg': 'hls.js',    'major': 1,  'file': 'hls.min.js',        'cdn_path': 'dist/hls.min.js'},
]


def _key(v):
    return [int(x) for x in v.split('.')]


def stable_versions(pkg):
    data = requests.get(f'https://registry.npmjs.org/{pkg}', timeout=15).json()
    return sorted((v for v in data['versions'] if '-' not in v), key=_key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--summary', help='write a Markdown summary of changes here')
    args = ap.parse_args()

    try:
        manifest = json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        manifest = {}

    ok = True
    changes, majors = [], []
    for lib in LIBS:
        pkg = lib['pkg']
        try:
            versions = stable_versions(pkg)
            in_major = [v for v in versions if v.startswith(f"{lib['major']}.")]
            if not in_major:
                raise RuntimeError(f"no {lib['major']}.x releases")
            ver, newest = in_major[-1], versions[-1]
            if _key(newest)[0] > lib['major']:
                majors.append(f'- `{pkg}` {newest} is out (vendored major: {lib["major"]}.x)')

            old = manifest.get(pkg)
            out = STATIC / lib['file']
            if old == ver and out.exists():
                print(f'  {pkg}@{ver}  (up to date)')
                continue
            url = f"https://cdn.jsdelivr.net/npm/{pkg}@{ver}/{lib['cdn_path']}"
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            out.write_text(resp.text, encoding='utf-8')
            manifest[pkg] = ver
            changes.append(f'- `{pkg}` {old or "?"} → {ver} '
                           f'([release notes](https://www.npmjs.com/package/{pkg}/v/{ver}))')
            print(f'  {pkg}@{old or "?"} → {ver}  →  static/{lib["file"]}')
        except Exception as e:
            print(f'  ERROR {pkg}: {e}', file=sys.stderr)
            ok = False

    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')

    if args.summary:
        parts = []
        if changes:
            parts += ['### Updated', *changes, '']
        if majors:
            parts += ['### New major versions (not installed)',
                      'Bump `major` in `scripts/update_vendor.py` after checking the changelog.',
                      *majors, '']
        Path(args.summary).write_text('\n'.join(parts))

    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
