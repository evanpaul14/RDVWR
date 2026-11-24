"""Best-effort language detection for post/comment text (drives the translate button).

Needs the optional `py3langid` (or `langdetect`) package; without it detect_lang() raises nothing and
returns UNAVAILABLE so the frontend falls back to showing the button everywhere."""
import re
import threading
from functools import lru_cache

from types import SimpleNamespace

# Preferred: py3langid (~1ms/call on a Pi, numpy). Fallback: langdetect (~13ms/call, pure
# Python). Neither installed -> detect_langs is None. Both give [obj(.lang, .prob)].
try:
    from py3langid.langid import LanguageIdentifier, MODEL_FILE
    _identifier = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)

    def detect_langs(text):
        lang, prob = _identifier.classify(text)
        return [SimpleNamespace(lang=lang, prob=float(prob))]
except ImportError:
    try:
        from langdetect import DetectorFactory, detector_factory
        DetectorFactory.seed = 0   # deterministic results
        detector_factory.init_factory()

        def detect_langs(text, n_trial=3):
            """langdetect.detect_langs with fewer random trials (default 7): ~2x faster."""
            d = detector_factory._factory.create()
            d.n_trial = n_trial
            d.append(text)
            return d.get_probabilities()
    except ImportError:        # pragma: no cover - exercised only when no detector is installed
        detect_langs = None

UNAVAILABLE = object()
MIN_LETTERS = 40      # shorter text is too ambiguous: langdetect calls short English "da"/"cy"/"af" at ~100%
MAX_CHARS = 250
MIN_PROB = 0.8
_STRIP_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)|https?://\S+|[/\\]?[ru]/\w+|[*_~`>#\[\]()|]')
# langdetect's codes -> the base codes browsers report in navigator.language
_ALIASES = {'zh-cn': 'zh', 'zh-tw': 'zh', 'nb': 'no', 'nn': 'no', 'iw': 'he'}


# Distinctive English function words (none are common words in other major languages). Most
# comments are English, and langdetect costs ~13ms a call on a Pi, so obvious English skips it.
_EN_WORDS = frozenset('the and that this with have not you they are but for just would what from your was were been their there about which when will'.split())
_WORD_RE = re.compile(r"[A-Za-z']+")


def _obviously_english(text):
    words = _WORD_RE.findall(text)
    if len(words) < 8 or not text.isascii():
        return False
    hits = sum(w.lower() in _EN_WORDS for w in words)
    return hits >= 3 and hits / len(words) >= 0.2


def warm_up():
    """Load the detector's model now (~2.5s on a Pi) instead of on the first request."""
    if detect_langs is not None:
        threading.Thread(target=lambda: detect_langs('warming up the language detector'), daemon=True).start()


def detect_lang(text):
    """Two-letter code of the dominant language, None if too short/uncertain, or
    UNAVAILABLE when no detector is installed."""
    if detect_langs is None:
        return UNAVAILABLE
    text = _STRIP_RE.sub(' ', text or '')[:MAX_CHARS]
    if sum(c.isalpha() for c in text) < MIN_LETTERS:
        return None
    return _detect(text)


@lru_cache(maxsize=4096)
def _detect(text):
    if _obviously_english(text):
        return 'en'
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
