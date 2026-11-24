"""Language detection for the translate button, with a fake detector (langdetect is optional)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS
from core import lang


def fake(code, prob=0.99):
    return lambda text: [NS(lang=code, prob=prob)]


def test_unavailable_without_detector(monkeypatch):
    monkeypatch.setattr(lang, 'detect_langs', None)
    assert lang.detect_lang('Bonjour tout le monde, comment ça va ?') is lang.UNAVAILABLE
    assert lang.lang_fields('Bonjour tout le monde, comment ça va ?') == {}


def test_detects_and_normalizes(monkeypatch):
    text = 'Det här är en ganska lång kommentar om bröd'
    monkeypatch.setattr(lang, 'detect_langs', fake('nb'))
    assert lang.lang_fields(text) == {'lang': 'no'}
    monkeypatch.setattr(lang, 'detect_langs', fake('zh-cn'))
    assert lang.detect_lang(text) == 'zh'


def test_short_or_uncertain_is_none(monkeypatch):
    monkeypatch.setattr(lang, 'detect_langs', fake('fr'))
    assert lang.lang_fields('lol ok') == {'lang': None}
    assert lang.lang_fields('https://example.com/some/long/path/with/letters') == {'lang': None}
    monkeypatch.setattr(lang, 'detect_langs', fake('fr', prob=0.5))
    assert lang.lang_fields('Bonjour tout le monde, comment ça va ?') == {'lang': None}


def test_detector_error_is_none(monkeypatch):
    def boom(text):
        raise RuntimeError('no features')
    monkeypatch.setattr(lang, 'detect_langs', boom)
    assert lang.lang_fields('Bonjour tout le monde, comment ça va ?') == {'lang': None}


def test_translate_button_default_setting():
    from core import helpers
    assert helpers.DEFAULT_SETTINGS['translateButton'] in ('auto', 'always', 'never')


def test_comment_fields_carry_lang(monkeypatch):
    from routes import comments
    monkeypatch.setattr(lang, 'detect_langs', fake('fr'))
    c = comments._parse_comment_fields({'id': 'a', 'body': 'Compliqué de trouver de la bonne farine pour le pain'})
    assert c['lang'] == 'fr'
