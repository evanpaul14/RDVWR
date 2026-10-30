"""Subreddit feeds from the Reddit Android app's GraphQL endpoint, for when Reddit's WAF
blocks the REST listing endpoints (a blocked IP gets a 403 page from every *.reddit.com JSON
URL, but gql-fed.reddit.com still answers).

The app's feed query (SubredditFeedSdui) is server-driven UI: it returns layout cells, not
posts. So a feed is two calls: the cells give the post ids and the next-page cursor, then
PostsByIds returns the full posts. Each is converted to the dict the REST listing would
have carried, so media_detection.process_post() handles both identically."""
import base64
import html as html_lib
import json
import re
from datetime import datetime
from gql_client import gql_query, _resolve_media
from gql_richtext import richtext_html
from helpers import FEED_LIMIT, UpstreamError, log
from media_detection import extract_posts

MAX_FEED_PAGES = 5   # SDUI pages hold ~7-18 posts; stop once a request has FEED_LIMIT of them
# Extra renditions / video fields PostsByIds only returns when asked for.
POSTS_FLAGS = {"includeExtendedVideoAsset": True, "includePlaybackMp4s": True,
               "includeExtraStillResolutions": True, "includeStillMediaAltText": True}
URL_RE = re.compile(r"(https?://[^\s<>\"]+)")


def _epoch(iso):
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else 0
    except ValueError:
        return 0


def _subreddit_of(permalink):
    """The community from a permalink, for profile posts (u/name -> "u_name")."""
    m = re.match(r"/(r|user)/([^/]+)/", permalink or "")
    return "" if not m else m.group(2) if m.group(1) == "r" else f"u_{m.group(2)}"


def _url(source):
    return (source or {}).get("url")


def _renditions(media):
    """[{u, x, y}] for a still / gallery image's sized copies, narrowest first."""
    out = []
    for key, source in media.items():
        dims = (source or {}).get("dimensions") if isinstance(source, dict) else None
        if key != "source" and dims and _url(source):
            out.append({"u": source["url"], "x": dims["width"], "y": dims["height"]})
    return sorted(out, key=lambda r: r["x"])


def _preview(still):
    """A REST `preview` block from `media.still`."""
    source = (still or {}).get("source")
    if not _url(source):
        return None
    return {"images": [{"source": {"url": source["url"], "width": source["dimensions"]["width"],
                                   "height": source["dimensions"]["height"]},
                        "resolutions": [{"url": r["u"], "width": r["x"], "height": r["y"]}
                                        for r in _renditions(still)]}]}


def _gallery(items):
    """(gallery_data, media_metadata) in REST's shape."""
    data, meta = [], {}
    for item in items:
        m = item.get("media") or {}
        mid = m.get("id")
        if not mid or not m.get("url"):
            continue
        data.append({"media_id": mid, "id": item.get("id"), "caption": item.get("caption") or ""})
        meta[mid] = {"status": "valid", "s": {"u": m["url"], "x": m.get("width", 0), "y": m.get("height", 0)},
                     "p": [{"u": r["u"], "x": r["x"], "y": r["y"]} for r in _renditions(m)]}
    return {"items": data}, meta


def _selftext_html(content):
    """Post body HTML, entity-escaped the way REST's selftext_html is (clean_reddit_html
    unescapes it before sanitizing). Rendered from the rich text; failing that, plain
    paragraphs with auto-linked URLs from the markdown."""
    body = richtext_html(content.get("richtext"),
                         {m["id"]: m["url"] for m in content.get("richtextMedia") or [] if m.get("url")})
    if not body:
        paras = []
        for para in re.split(r"\n\s*\n", (content.get("markdown") or "").strip()):
            text = html_lib.escape(para.strip()).replace("\n", "<br>")
            paras.append("<p>" + URL_RE.sub(r'<a href="\1">\1</a>', text) + "</p>")
        body = "".join(paras)
    return html_lib.escape(body)


def _video_fields(p, n, media):
    """Reddit-hosted video, the Reddit copy of an external clip, and embeds, in REST's shapes."""
    streaming, packaged = media.get("streaming"), media.get("packagedMedia") or {}
    if streaming:
        muxed = packaged.get("muxedMp4s") or {}
        video = {"fallback_url": _url(muxed.get("recommended")) or _url(muxed.get("medium"))
                                 or _url(media.get("download")),
                 "hls_url": streaming.get("hlsUrl"), "is_gif": bool(streaming.get("isGif"))}
        if n.get("domain") == "v.redd.it":
            p["is_video"] = True
            p["media"] = {"reddit_video": video}
        else:   # RedGifs, Imgur...: REST only exposes Reddit's copy as a preview, is_video stays False
            p["preview"] = {**(p.get("preview") or {"images": []}), "reddit_video_preview": video}
    if media.get("typeHint") == "EMBED":
        embed = media.get("video") or {}
        dims = embed.get("dimensions") or {}
        p["secure_media_embed"] = {"media_domain_url": f"https://www.redditmedia.com/mediaembed/{p['id']}",
                                   "height": dims.get("height"), "width": dims.get("width")}
        if embed.get("embedHtml"):
            p["secure_media"] = {"oembed": {"html": embed["embedHtml"],
                                            "provider_name": (embed.get("attribution") or {}).get("providerName", ""),
                                            "height": dims.get("height"), "width": dims.get("width")}}


def gql_post_to_rest(n):
    """A PostsByIds post as the dict a REST listing child's `data` would hold."""
    media = n.get("media") or {}
    flair = n.get("flair") or {}
    try:
        richtext = json.loads(flair.get("richtext") or "[]")
    except ValueError:
        richtext = []
    content = n.get("content") or {}
    p = {
        "id": n["id"].removeprefix("t3_"),
        "title": n.get("postTitle", ""),
        "author": (n.get("authorInfo") or {}).get("name") or "[deleted]",
        "subreddit": (n.get("subreddit") or {}).get("name") or _subreddit_of(n.get("permalink")),
        "score": n.get("score") or 0,
        "likes": {"UPVOTE": True, "DOWNVOTE": False}.get(n.get("voteState")),
        "upvote_ratio": n.get("upvoteRatio") or 0,
        "num_comments": n.get("commentCount") or 0,
        "created_utc": int(_epoch(n.get("createdAt"))),
        "url": n.get("url") or _url(n.get("outboundLink")) or "",
        "permalink": n.get("permalink", ""),
        "is_self": bool(n.get("isSelfPost")),
        "selftext": _resolve_media(content),
        "selftext_html": _selftext_html(content) if content.get("markdown") else "",
        "is_video": False,
        "post_hint": {"IMAGE": "image", "VIDEO": "hosted:video", "EMBED": "rich:video",
                      "GIFVIDEO": "image"}.get(media.get("typeHint"), ""),
        "over_18": bool(n.get("isNsfw")),
        "link_flair_text": flair.get("text") or "",
        "link_flair_richtext": richtext,
        "link_flair_type": flair.get("type") or "text",
        "link_flair_background_color": ((flair.get("template") or {}).get("backgroundColor") or "").lower(),
        "link_flair_text_color": (flair.get("textColor") or "dark").lower(),
        "domain": n.get("domain", ""),
        "stickied": bool(n.get("isStickied")),
        "spoiler": bool(n.get("isSpoiler")),
        "locked": bool(n.get("isLocked")),
        "edited": _epoch(n.get("editedAt")) or False,
        "all_awardings": [{"name": a["award"].get("name", ""), "count": a.get("total", 1),
                           "static_icon_url": _url(a["award"].get("static_icon_32"))
                                              or _url(a["award"].get("static_icon_24")) or ""}
                          for a in n.get("awardings") or [] if a.get("award")],
    }
    preview = _preview(media.get("still"))
    if preview:
        p["preview"] = preview
    _video_fields(p, n, media)
    items = (n.get("gallery") or {}).get("items")
    if items:
        p["is_gallery"] = True
        p["gallery_data"], p["media_metadata"] = _gallery(items)
    poll = n.get("poll")
    if poll:
        options = poll.get("options") or []
        p["poll_data"] = {
            "options": [{"id": o.get("id", ""), "text": o.get("text", ""), "vote_count": o.get("voteCount")}
                        for o in options],
            "total_vote_count": sum(o.get("voteCount") or 0 for o in options),
            "voting_end_timestamp": int(_epoch(poll.get("votingEndsAt")) * 1000)}
    root = (n.get("crosspostRoot") or {}).get("post")
    if root:
        p["crosspost_parent_list"] = [gql_post_to_rest(root)]
    return p


_POST_ID_RE = re.compile(r"-(t3_\w+)$")


def _cell_post_id(cell):
    """The post a feed cell belongs to: cell ids are base64("<Kind>Cell-t3_<post id>")."""
    try:
        m = _POST_ID_RE.search(base64.b64decode(cell.get("id") or "").decode())
    except (ValueError, UnicodeDecodeError):
        return None
    return m and m.group(1)


def _feed_page(operation, root, variables, missing, timeout):
    """(post ids, next cursor) for one SDUI page. `root` is the query's top field, whose
    `elements` hold the cells; a missing target answers elements: null."""
    data = gql_query(operation, variables, timeout)
    elements = ((data.get("data") or {}).get(root) or {}).get("elements")
    if not elements:
        raise UpstreamError(missing, 404, "not_found")
    ids = []
    for edge in elements.get("edges") or []:
        node = edge.get("node") or {}
        if node.get("adPayload"):
            continue
        post_id = next((i for i in map(_cell_post_id, node.get("cells") or []) if i), None)
        if post_id:
            ids.append(post_id)
    page = elements.get("pageInfo") or {}
    return ids, (page.get("endCursor") or None) if page.get("hasNextPage") is not False else None


def _listing(operation, root, variables, missing, after, timeout):
    """{"posts", "after"}: ids from SDUI pages until FEED_LIMIT of them, then their posts."""
    ids, cursor = [], after or None
    for _ in range(MAX_FEED_PAGES):
        page_ids, cursor = _feed_page(operation, root, {**variables, **({"after": cursor} if cursor else {})},
                                      missing, timeout)
        ids += [i for i in page_ids if i not in ids]
        if not cursor or len(ids) >= FEED_LIMIT:
            break
    if not ids:
        return {"posts": [], "after": cursor}
    data = gql_query("PostsByIds", {"ids": ids, **POSTS_FLAGS}, timeout)
    by_id = {n["id"]: n for n in (data.get("data") or {}).get("postsInfoByIds") or []
             if n.get("postTitle") is not None}
    return {"posts": posts_from_nodes([by_id[i] for i in ids if i in by_id]), "after": cursor}


def posts_from_nodes(nodes):
    """process_post()ed posts (NSFW-filtered like extract_posts) from GraphQL post nodes."""
    children = []
    for n in nodes:
        try:
            children.append({"kind": "t3", "data": gql_post_to_rest(n)})
        except (KeyError, TypeError, AttributeError) as e:
            log.warning("gql feed: skipping malformed post %s: %s", n.get("id"), e)
    return extract_posts({"children": children})


def _sorted(sort, t):
    variables = {"sort": sort.upper()}
    if t:
        variables["time"] = t.upper()
    return variables


def fetch_gql_feed(subreddit, sort, t="", after="", timeout=15):
    """{"posts", "after"} for a single subreddit (r/all and r/popular use the popular feed):
    the same dict fetch_feed returns, minus link hydration. Raises UpstreamError when Reddit
    has no such subreddit and RuntimeError when the app's queries stop answering."""
    if subreddit.lower() in ("all", "popular"):
        return _listing("PopularFeedSdui", "popularV3", _sorted(sort, t), "Subreddit not found", after, timeout)
    return _listing("SubredditFeedSdui", "subredditV3", {"subredditName": subreddit, **_sorted(sort, t)},
                    "Subreddit not found", after, timeout)


def fetch_gql_frontpage(sort, t="", after="", timeout=15):
    """The logged-out front page, as fetch_frontpage returns it: the app's popular feed."""
    return _listing("PopularFeedSdui", "popularV3", _sorted(sort, t), "Front page not found", after, timeout)


def fetch_gql_user_posts(username, sort="new", t="", after="", timeout=15):
    """A user's submissions: {"posts", "after"}. Raises UpstreamError when there's no such user."""
    return _listing("SubmittedPostsFeedSdui", "profileFeed", {"username": username, **_sorted(sort, t)},
                    "User not found or profile is private", after, timeout)
