"""Server-side counterpart of static/render.js renderMd.

Parse raw bodies rather than Reddit's sometimes incomplete *_html fields. Generated
HTML still passes through the no-JS allowlist before it reaches a template.
"""
import re

import mistune

from parsing.reddit_html import sanitize_reddit_html


class _Renderer(mistune.HTMLRenderer):
    def image(self, text, url, title=None):
        url = url.replace('%7C', '|').replace('%7c', '|')
        if url.startswith('giphy|'):
            url = f'https://media.giphy.com/media/{url[6:]}/giphy.gif'
        elif url.startswith('redgifs|'):
            return self.link(text or 'video', 'https://www.redgifs.com/watch/' + url[8:])
        elif url.startswith('redditvid|'):
            # The JS player muxes separate audio/video streams. Offer the original
            # player when that machinery isn't available.
            return self.link(text or 'video', 'https://v.redd.it/' + url[10:])
        return super().image(text, url, title)


def _reddit_syntax(md):
    def parse_mention(inline, match, state):
        label = match.group('mention_text').lstrip('/')
        path = '/user/' + label[2:] if label.startswith('u/') else '/' + label
        if state.in_link:
            state.append_token({'type': 'text', 'raw': match.group(0)})
        else:
            state.append_token({'type': 'link', 'children': [{'type': 'text', 'raw': label}],
                                'attrs': {'url': path}})
        return match.end()

    def parse_autolink(inline, match, state):
        text = match.group('autolink_text').rstrip('.,:;!?')
        if state.in_link:
            state.append_token({'type': 'text', 'raw': text})
        else:
            url = 'http://' + text if text.startswith('www.') else 'mailto:' + text
            state.append_token({'type': 'link', 'children': [{'type': 'text', 'raw': text}],
                                'attrs': {'url': url}})
        return match.start() + len(text)

    md.inline.register('reddit_mention',
                       r'(?<![\w/])(?P<mention_text>/?(?:r/[A-Za-z0-9_]+(?:/comments/[A-Za-z0-9_]+)?|u/[A-Za-z0-9_-]+))',
                       parse_mention, before='link')
    md.inline.register('gfm_autolink',
                       r'(?<![\w/])(?P<autolink_text>www\.[A-Za-z0-9][^\s<>]*|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})',
                       parse_autolink, before='link')

    def parse_sup(inline, match, state):
        text = match.group('sup_group')
        if text is None:
            text = match.group('sup_word')
            if not text.strip('^'):
                state.append_token({'type': 'text', 'raw': match.group(0)})
                return match.end()
            # Bare chained carets nest, as in render.js's _nestSup.
            text = text.replace('^', '<sup>') + '</sup>' * text.count('^')
        child = state.copy()
        child.src = text
        state.append_token({'type': 'superscript', 'children': inline.render(child)})
        return match.end()

    md.inline.register('reddit_sup', r'\^(?:\((?P<sup_group>[^)]*)\)|(?P<sup_word>\S+))', parse_sup, before='link')
    md.renderer.register('superscript', lambda renderer, text: '<sup>' + text + '</sup>')


_markdown = mistune.create_markdown(
    renderer=_Renderer(escape=False), hard_wrap=True,
    plugins=['table', 'strikethrough', 'task_lists', 'url', _reddit_syntax],
)
_SPOILER = re.compile(r'(```[\s\S]*?```|`[^`]*`)|>!([\s\S]*?)(?:!<|$)')
_CODE_OR_URL = re.compile(r'(```[\s\S]*?```|`[^`]*`)|(https?://\S+)')


def render_markdown(text):
    if not text:
        return ''
    # Match renderMd's fix for Reddit's escaped underscores in bare URLs.
    text = _CODE_OR_URL.sub(lambda m: m[1] if m[1] is not None else m[2].replace(r'\_', '_'), text)
    text = _SPOILER.sub(lambda m: m[1] if m[1] is not None else '<span class="md-spoiler-text">' + m[2] + '</span>', text)
    return sanitize_reddit_html(_markdown(text))
