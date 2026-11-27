"""Markdown features shared by renderMd and the server-rendered body filters."""
import pytest
from bs4 import BeautifulSoup

from app import app
from core.template_helpers import mdbody, mdtext
from parsing.markdown import render_markdown


def soup(text):
    return BeautifulSoup(render_markdown(text), 'html.parser')


def test_gfm_blocks_and_inline_formatting():
    doc = soup('# Heading\n\n**bold** *italic* ~~gone~~\nnext\n\n'
               '> quote\n\n3. third\n4. fourth\n\n'
               '- [x] done\n- [ ] pending\n\n'
               '| left | right |\n| :--- | ---: |\n| a | b |\n\n'
               '---\n\n```python\nprint("hi")\n```')
    for selector, text in [('h1', 'Heading'), ('strong', 'bold'), ('em', 'italic'),
                           ('del', 'gone'), ('blockquote', 'quote'), ('pre code', 'print("hi")')]:
        assert doc.select_one(selector).get_text().strip() == text
    assert doc.select_one('br')
    assert doc.select_one('ol')['start'] == '3'
    boxes = doc.select('input')
    assert len(boxes) == 2 and all(b.has_attr('disabled') for b in boxes)
    assert boxes[0].has_attr('checked') and not boxes[1].has_attr('checked')
    assert doc.select_one('th')['style'] == 'text-align:left'
    assert doc.select_one('tbody td').get_text() == 'a'
    assert doc.select_one('hr')


def test_reddit_syntax_and_code_isolation():
    doc = soup('>!**secret**!< ^(two words) ^a^b^c \\^literal\n\n'
               'r/python u/some_one\n\n'
               '`>!visible!< ^plain r/code`\n\n````\n>!visible!< ^plain u/code\n````')
    assert doc.select_one('.spoiler strong').get_text() == 'secret'
    assert doc.select_one('.spoiler')['tabindex'] == '0'
    assert doc.select_one('sup').get_text() == 'two words'
    assert doc.select_one('sup sup sup').get_text() == 'c'
    assert '^literal' in doc.get_text()
    assert doc.select_one('a[href="/r/python"]')
    assert doc.select_one('a[href="/user/some_one"]')
    for code in doc.select('code'):
        assert not code.select('span, sup, a')
        assert '>!visible!< ^plain' in code.get_text()


def test_links_images_and_escaped_url_underscores():
    doc = soup('[**label**](https://example.com/page)\n\n'
               'https://example.com/a\\_b\n\n![image](https://i.redd.it/a.png)\n\n'
               '![gif](giphy|abc123)')
    assert doc.select_one('a strong').get_text() == 'label'
    assert doc.select_one('a[href="https://example.com/a_b"]')
    assert '/api/img?' in doc.select_one('img')['src']
    assert doc.select('img')[1]['src'] == 'https://media.giphy.com/media/abc123/giphy.gif'


def test_reddit_media_viewer_links_render_the_wrapped_image():
    doc = soup('[that image](https://www.reddit.com/media?url=https%3A%2F%2Fi.redd.it%2Fvlha9ew7e0ke1.jpeg)\n\n'
               '[page](https://www.reddit.com/media?url=https%3A%2F%2Fexample.com%2Fpage)')
    assert doc.select_one('img')['src'] == '/api/img?url=https%3A%2F%2Fi.redd.it%2Fvlha9ew7e0ke1.jpeg'
    assert doc.select_one('.md-img-caption').get_text() == 'that image'
    assert len(doc.select('img')) == 1


def test_mp4_gif_previews_render_as_looping_video():
    doc = soup('[gif](https://www.reddit.com/media?url=https%3A%2F%2Fexternal-preview.redd.it%2Fabc.gif'
               '%3Fwidth%3D400%26format%3Dmp4%26s%3Ddef)')
    video = doc.select_one('video.md-gif-video')
    assert video['src'] == ('/api/img?url=https%3A%2F%2Fexternal-preview.redd.it%2Fabc.gif'
                            '%3Fwidth%3D400%26format%3Dmp4%26s%3Ddef')
    assert video.has_attr('autoplay') and video.has_attr('loop') and video.has_attr('muted')
    assert not doc.select('img')
    assert doc.select_one('.md-img-caption').get_text() == 'gif'


@pytest.mark.parametrize('text', [
    '<script>alert(1)</script><style>bad</style>**ok**',
    '<img src="javascript:alert(1)" onerror="alert(1)">',
    '[bad](javascript:alert%281%29)',
    '![bad](data:image/svg+xml,bad)',
    '<input type="text" autofocus onfocus="alert(1)">',
    '>!<img src="https://example.com/a.png" onerror="alert(1)">!<',
])
def test_untrusted_markdown_is_sanitized(text):
    doc = soup(text)
    assert not doc.select('script, style, input:not([disabled])')
    for tag in doc.find_all(True):
        assert not any(key.startswith('on') for key in tag.attrs)
        for attr in ('href', 'src'):
            assert not tag.get(attr, '').startswith(('javascript:', 'data:'))


def test_body_prefers_markdown_and_preserves_html_only_fallback():
    with app.test_request_context('/'):
        assert '<strong>raw</strong>' in mdbody('**raw**', '<p>incomplete</p>')
        assert '<strong>html only</strong>' in mdbody('', '<strong>html only</strong>')
        assert '<del>deleted</del>' in mdtext('~~deleted~~')
    with app.test_request_context('/', headers={'Cookie': 'ns_link_external_media=1'}):
        body = mdbody('![image](https://example.com/image.png)')
        assert '<img' not in body
        assert 'href="https://example.com/image.png"' in body


def test_gfm_autolinks_reddit_comment_paths_and_image_link_captions():
    doc = soup('www.example.com, tester@example.com r/python/comments/abc123\n\n'
               '[caption](https://example.com/photo.png)\n\n'
               '[r/python](https://example.com/)')
    assert doc.select_one('a[href="http://www.example.com"]')
    assert doc.select_one('a[href="mailto:tester@example.com"]')
    assert doc.select_one('a[href="/r/python/comments/abc123"]')
    assert doc.select_one('.md-img-caption').get_text() == 'caption'
    assert len(doc.select('a[href="https://example.com/"]')) == 1
    assert not doc.select('a a')
