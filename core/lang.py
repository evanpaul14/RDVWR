"""Best-effort language detection for post/comment text (drives the translate button).

Needs the optional `langdetect` package; without it detect_lang() raises nothing and
returns UNAVAILABLE so the frontend falls back to showing the button everywhere."""
import re

try:
    from langdetect import DetectorFactory, detect_langs
    DetectorFactory.seed = 0   # deterministic results
except ImportError:            # pragma: no cover - exercised only when the package is absent
    detect_langs = None

UNAVAILABLE = object()
MIN_LETTERS = 40      # shorter text is too ambiguous: langdetect calls short English "da"/"cy"/"af" at ~100%
MAX_CHARS = 400
MIN_PROB = 0.8
_STRIP_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)|https?://\S+|[/\\]?[ru]/\w+|[*_~`>#\[\]()|]')
# langdetect's codes -> the base codes browsers report in navigator.language
_ALIASES = {'zh-cn': 'zh', 'zh-tw': 'zh', 'nb': 'no', 'nn': 'no', 'iw': 'he'}


def detect_lang(text):
    """Two-letter code of the dominant language, None if too short/uncertain, or
    UNAVAILABLE when no detector is installed."""
    if detect_langs is None:
        return UNAVAILABLE
    text = _STRIP_RE.sub(' ', text or '')[:MAX_CHARS]
    if sum(c.isalpha() for c in text) < MIN_LETTERS:
        return None
    try:
        best = detect_langs(text)[0]
    except Exception:
        return None
    if best.prob < MIN_PROB:
        return None
    return _ALIASES.get(best.lang, best.lang)


def lang_fields(text):
    """{'lang': code-or-None} for a payload, or {} when detection is unavailable."""
    lang = detect_lang(text)
    return {} if lang is UNAVAILABLE else {'lang': lang}
