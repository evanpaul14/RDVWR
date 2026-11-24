"""Machine translation of post/comment text (POST /api/translate), via Google's keyless web endpoint."""
import re
import hashlib
from flask import Blueprint, jsonify, request, abort
from curl_cffi import requests as cffi_requests
from core.helpers import TTLCache, _CACHE_MISS, is_same_site_request, log

bp = Blueprint("translate", __name__)

TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
LANG_RE = re.compile(r'^[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?$')
MAX_TEXTS = 10
MAX_TEXT_LEN = 10000
CHUNK_LEN = 3000
CACHE_TTL = 86400
_cache = TTLCache(500, name='translate')


def _chunks(text):
    """Split on paragraph/line boundaries so each piece stays under CHUNK_LEN."""
    out, cur = [], ''
    for para in text.split('\n'):
        while len(para) > CHUNK_LEN:
            if cur:
                out.append(cur)
                cur = ''
            out.append(para[:CHUNK_LEN])
            para = para[CHUNK_LEN:]
        if cur and len(cur) + len(para) + 1 > CHUNK_LEN:
            out.append(cur)
            cur = para
        else:
            cur = f'{cur}\n{para}' if cur else para
    if cur:
        out.append(cur)
    return out


def _translate_chunk(chunk, target):
    """Returns (translated, detected_source_lang). Raises curl_cffi RequestException on failure."""
    r = cffi_requests.post(TRANSLATE_URL, params={'client': 'gtx', 'sl': 'auto', 'tl': target, 'dt': 't', 'dj': '1'},
                      data={'q': chunk}, impersonate='chrome131', timeout=10)
    r.raise_for_status()
    data = r.json()
    return ''.join(s.get('trans', '') for s in data.get('sentences', [])), data.get('src', '')


def translate_text(text, target):
    key = hashlib.sha1(f'{target}\0{text}'.encode()).hexdigest()
    hit = _cache.get(key)
    if hit is not _CACHE_MISS:
        return hit
    parts, src = [], ''
    for chunk in _chunks(text):
        if not chunk.strip():
            parts.append(chunk)
            continue
        out, s = _translate_chunk(chunk, target)
        parts.append(out)
        src = src or s
    result = {'text': '\n'.join(parts), 'source': src}
    _cache.set(key, result, CACHE_TTL)
    return result


@bp.route("/api/translate", methods=["POST"])
def api_translate():
    if not is_same_site_request():
        abort(403)
    body = request.get_json(silent=True) or {}
    texts, target = body.get('texts'), str(body.get('to') or 'en')
    if (not isinstance(texts, list) or not 0 < len(texts) <= MAX_TEXTS
            or not all(isinstance(t, str) and len(t) <= MAX_TEXT_LEN for t in texts)
            or not LANG_RE.match(target)):
        return jsonify({'error': 'Invalid request'}), 400
    try:
        results = [translate_text(t, target) if t.strip() else {'text': t, 'source': ''} for t in texts]
    except (cffi_requests.exceptions.RequestException, ValueError) as e:
        log.warning('translate failed: %s', e)
        return jsonify({'error': 'Translation service unavailable'}), 502
    resp = jsonify({'texts': [r['text'] for r in results], 'source': next((r['source'] for r in results if r['source']), '')})
    resp.headers['Cache-Control'] = 'no-store'
    return resp
