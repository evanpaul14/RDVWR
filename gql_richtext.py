"""A GraphQL post body's rich text (Reddit's RTJSON document) as HTML.

REST's selftext_html is Reddit's own rendering of the markdown; the app's `content.html` is
bare text, but `content.richtext` is the structured document the app draws from, so rendering
that gives the no-JS pages real paragraphs, lists, links, tables and code. The output is plain
HTML; reddit_html.clean_reddit_html sanitizes it (it expects the entity-escaped form REST
sends, so gql_posts escapes this once more)."""
import html as html_lib
import json

# inline format bit -> tag, innermost first
FORMATS = ((64, "code"), (32, "sup"), (8, "del"), (2, "em"), (1, "strong"))


def _esc(text):
    return html_lib.escape(text or "")


def _formatted(text, ranges):
    """`text` with its [format bits, start, length] ranges as tags. Offsets count UTF-16 code
    units, since Reddit's editor is JavaScript."""
    if not ranges:
        return _esc(text)
    units = text.encode("utf-16-le")
    size = len(units) // 2
    spans = [(int(f), max(int(s), 0), min(int(s) + int(n), size)) for f, s, n in ranges]
    cuts = sorted({0, size} | {c for _, s, e in spans for c in (s, e)})
    out = []
    for a, b in zip(cuts, cuts[1:]):
        segment = _esc(units[2 * a:2 * b].decode("utf-16-le", "replace"))
        bits = 0
        for f, s, e in spans:
            if s <= a and b <= e:
                bits |= f
        for bit, tag in FORMATS:
            if bits & bit:
                segment = f"<{tag}>{segment}</{tag}>"
        out.append(segment)
    return "".join(out)


def _children(node, media):
    return "".join(_node(c, media) for c in node.get("c") or [])


def _cells(cells, tag, media):
    return "".join(f"<{tag}>{_children(c, media)}</{tag}>" for c in cells or [])


def _node(n, media):
    kind = n.get("e")
    if kind == "text":
        return _formatted(n.get("t", ""), n.get("f"))
    if kind == "raw":
        return _esc(n.get("t", ""))
    if kind == "par":
        return f"<p>{_children(n, media)}</p>"
    if kind == "br":
        return "<br>"
    if kind == "hr":
        return "<hr>"
    if kind == "link":
        url = n.get("u", "")
        return f'<a href="{html_lib.escape(url, quote=True)}">{_esc(n.get("t") or url)}</a>'
    if kind in ("u/", "r/"):
        name = _esc(n.get("t", ""))
        return f'<a href="/{kind}{name}">{kind}{name}</a>'
    if kind == "h":
        level = min(max(int(n.get("l") or 1), 1), 6)
        return f"<h{level}>{_children(n, media)}</h{level}>"
    if kind == "blockquote":
        return f"<blockquote>{_children(n, media)}</blockquote>"
    if kind == "list":
        tag = "ol" if n.get("o") else "ul"
        return f"<{tag}>{_children(n, media)}</{tag}>"
    if kind == "li":
        return f"<li>{_children(n, media)}</li>"
    if kind == "code":
        return "<pre><code>" + "\n".join(_esc(c.get("t", "")) for c in n.get("c") or []) + "</code></pre>"
    if kind == "spoilertext":
        return f'<span class="md-spoiler-text">{_children(n, media)}</span>'
    if kind == "img":
        url = media.get(n.get("id"))
        return f'<p><a href="{html_lib.escape(url, quote=True)}">{_esc(url)}</a></p>' if url else ""
    if kind == "table":
        rows = "".join(f"<tr>{_cells(row, 'td', media)}</tr>" for row in n.get("c") or [])
        return (f"<table><thead><tr>{_cells(n.get('h'), 'th', media)}</tr></thead>"
                f"<tbody>{rows}</tbody></table>")
    return _children(n, media)   # an element type we don't know: keep its content


def richtext_html(richtext, media_urls=None):
    """HTML for an RTJSON document (the JSON string `content.richtext`), or "" when it
    isn't one. `media_urls` maps an `img` element's id to its URL."""
    try:
        document = json.loads(richtext)["document"]
        return "".join(_node(n, media_urls or {}) for n in document)
    except (ValueError, KeyError, TypeError, AttributeError):
        return ""
