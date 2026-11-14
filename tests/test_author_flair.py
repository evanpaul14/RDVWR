import pytest
from flask import render_template_string

from app import app
from core.template_helpers import author_flair_color


@pytest.mark.parametrize('bg,preference,expected', [
    ('#0D0D0D', 'dark', '#fff'),  # Reported comment: nearly invisible dark text.
    ('#000000', 'light', '#fff'),
    ('#808080', 'dark', '#1a1a1a'),
    ('#01A1FE', 'dark', '#1a1a1a'),
    ('#01A1FE', 'light', '#1a1a1a'),
])
def test_author_flair_contrast(bg, preference, expected):
    assert author_flair_color(bg, preference) == expected


def test_noscript_reported_author_flair():
    with app.test_request_context('/'):
        html = render_template_string(
            '{% from "noscript/macros/posts.html" import author_flair %}{{ author_flair(c) }}',
            c={'author_flair_text': 'Air Fryer Aristocrat ', 'author_flair_bg': '#0D0D0D',
               'author_flair_tc': 'dark', 'author_flair_type': 'text', 'author_flair_richtext': []})
    assert 'background:#0D0D0D;color:#fff' in html
    assert 'Air Fryer Aristocrat' in html
