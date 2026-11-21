"""A subreddit's wiki page from the Reddit Android app's GraphQL endpoint, for when Reddit's
REST wiki JSON answers 403 (it does for anonymous and device-token clients alike). Returns
the dict routes/subreddit.fetch_wiki builds. The heading ids and table of contents follow
Reddit's own wiki HTML (ported from Nachtalb/redlib) so section links keep working."""
import html as html_lib
import re
from gql.client import gql_query
from gql.richtext import richtext_html
from core.helpers import UpstreamError

_HEADING_RE = re.compile(r"<h([1-6])>(.*?)</h[1-6]>", re.S)
_TAG_RE = re.compile(r"<[^>]*>")
_ID_SAFE = frozenset(b"abcdefghijklmnopqrstuvwxyz0123456789_.-")


def _heading_id(text, seen):
    """Reddit's wiki anchor for a heading: "wiki_" + the lowercased text with spaces as
    underscores and every other non-alphanumeric byte as .XX; repeats get a counter."""
    out = ["wiki_"]
    for byte in text.replace(" ", "_").lower().encode():
        out.append(chr(byte) if byte in _ID_SAFE else f".{byte:02X}")
    heading_id = "".join(out)
    seen[heading_id] = seen.get(heading_id, 0) + 1
    return heading_id + (str(seen[heading_id]) if seen[heading_id] > 1 else "")


def wiki_toc(body):
    """`body` with ids on its headings and Reddit's nested table of contents in front."""
    seen = {}
    lists = [[0, ""]]   # open TOC lists: [heading level it started at, its <li> items]
    previous = 0

    def close():
        _, items = lists.pop()
        lists[-1][1] += f'<li class="toc_child"><ul>{items}</ul></li>'

    def heading(m):
        nonlocal previous
        level, inner = int(m.group(1)), m.group(2)
        text = html_lib.unescape(_TAG_RE.sub("", inner))
        if not text:
            return m.group(0)
        hid = _heading_id(text, seen)
        if previous and level > previous:
            lists.append([level, ""])
        else:
            while len(lists) > 1 and lists[-1][0] > level:
                close()
        previous = level
        lists[-1][1] += f'<li class="{hid}"><a href="#{hid}">{html_lib.escape(text, quote=False)}</a></li>'
        return f'<h{level} id="{hid}">{inner}</h{level}>'

    out = _HEADING_RE.sub(heading, body)
    if not previous:
        return body
    while len(lists) > 1:
        close()
    return f'<div class="toc"><ul>{lists[0][1]}</ul></div>{out}'


def fetch_gql_wiki(subreddit, page, timeout=15):
    """The dict fetch_wiki returns. Raises UpstreamError for a missing page or an
    unavailable subreddit; the app doesn't send a revision date, so it's None."""
    data = gql_query("SubredditWiki", {
        "subredditName": subreddit, "pageName": page, "includePage": True,
        "includePageContent": True, "includeMediaFragment": True, "includeIsRevisable": False,
        "includeRevisionsCount": False, "includePageTitle": False}, timeout)
    node = (data.get("data") or {}).get("subredditInfoByName") or {}
    if node.get("__typename") != "Subreddit":
        raise UpstreamError("Wiki is private or disabled", 403)
    wiki_page = (node.get("wiki") or {}).get("page") or {}
    if wiki_page.get("status") != "VALID":
        raise UpstreamError("Wiki page not found", 404)
    content = wiki_page.get("content") or {}
    media = {m["id"]: m["url"] for m in content.get("richtextMedia") or [] if m.get("url")}
    body = richtext_html(content.get("richtext"), media)
    return {"content_html": f'<div class="md wiki">{wiki_toc(body)}</div>', "revision_date": None}
