# CLAUDE.md

## Running

```bash
pip install -r requirements.txt   # exact pins; Dependabot bumps them via weekly PRs
python app.py          # http://localhost:8002
python3 -m pytest tests/
node --test tests/*.mjs   # frontend module tests
```

Python tests are named after what they cover; the big ones are `test_routes.py` (API endpoints, caching, same-site gate), `test_media_detection.py` (`process_post`), `test_noscript.py` / `test_noscript_parity.py` (server-rendered pages and no-JS fallback, and that they match the JS app's settings) and `test_gql_feed.py` (GraphQL fallback). The `.mjs` tests cover frontend modules that don't need a DOM (`hydration`, `home-pagination`, `homefeed-seen`, `media-lifecycle`, navigation).

Do not install or run Playwright (or any other browser-automation tool) to verify frontend changes unless explicitly asked to — ask first.

## Architecture

Single-page Reddit viewer. Python/Flask backend proxies Reddit API; ES module frontend.

**Backend files:** `app.py` (Flask app setup + blueprint registration only) at the root, plus packages:
- `routes/` — one blueprint per area: `media`, `downloads`, `search`, `subreddit`, `home`, `comments`, `avatars`, `users`, `live`, `embeds`, `pages`, `ns_settings`, `mediaproxy`, `passthrough`, `account`, `health`, `stats`, `rss`, `translate`, plus the logged-in account ones `auth` (login), `actions` (vote/join/comment) and `posting` (submit) (registered in `routes/__init__.py`); plus `routes/page_data.py`, not a blueprint, which builds the data for `pages`.
- `core/` — `config.py` (**every** environment setting, the allowed sort/time/theme/layout values and `DEFAULT_SETTINGS`; imports nothing from the project), `cache.py` (`TTLCache`, `CACHE_MISS`, `@server_cache`, `@fetch_cache`, `cached_json`, `CACHE_TTL_*`, the optional Redis client), `urls.py` (`safe_next`, `checked_url_host`), `helpers.py` (allowlist regexes, limits, `UpstreamError`, `validate_params`, `error_response`, `is_same_site_request`, `parallel`, `hydrate_linked_posts`, `feed_time`), `template_helpers.py` (Jinja filters/globals), `ns_prefs.py` (noscript preference cookies), `filters.py` (visitor post filters by subreddit/user/keyword, mirrored by `static/filters.js`), `backup.py` (settings backup code / `/settings/restore` link format, mirrored by `static/backup.js`), `base_path.py`, `ratelimit.py` (per-IP request limits), `lang.py` (language detection for the translate button), `stats.py` (in-process request/upstream/cache counters for `/stats`; upstream calls are recorded by `reddit.budget.request`/`timed`).
- `parsing/` — `media_detection.py` (`process_post` and media helpers), `reddit_html.py` (sanitizer for Reddit's `*_html` fields), `shreddit.py` (shreddit HTML post parsers), `markdown.py` (server-side counterpart of `renderMd`).
- `reddit/` — `client.py` (OAuth via Android device-token spoofing — rotating pool of 3 identities, tokens refresh every ~10 min; disable with `REDDIT_OAUTH=0`), `login.py`, `actions.py`, `media.py`, `owner.py`, `budget.py` (upstream budget), `waf.py` (WAF cooldown: pauses upstream calls after a burst of Reddit block pages; `reddit_get(..., waf_key=...)` skips an outright-blocked endpoint for an hour), `archive.py` (Arctic Shift fallback for user profiles), `scrape.py` (post ids of combined-feed New/Rising sorts from Reddit's HTML listing).
- `gql/` — GraphQL fallback (`client.py`, `posts.py`, `search.py`, `subreddit.py`, `user.py`, `wiki.py`, `richtext.py`) and `gql_hashes.json`.
- `scripts/` — `update_vendor.py` (updates vendored JS within pinned majors, versions recorded in `static/vendor.json`; run weekly by `.github/workflows/update-vendor.yml`, which opens a PR), `update_gql_hash.py`, `benchmark.py`, `benchmark_pages.py`.
Import names from the module that defines them (`from core.config import DISABLE_DOWNLOADS`, `from core.cache import fetch_cache`, `from reddit import client as reddit_client`); new Python files go in the matching package. A new environment variable goes in `core/config.py`, not in an `os.environ` read in the module that uses it.

`process_post()` normalizes post dicts; `_media()` picks the media fields, with one small helper per kind (`_gallery`, `_reddit_video`, `_embed`, `_gif`, …). Media detection priority: galleries → redgifs → reddit HLS video → reddit_video_preview → YouTube → Streamable → TikTok → Streamin → iframe → Imgur album → GIF/GIFV/Imgur image.

Keep each Python file under ~500 lines — add new endpoints to the matching `routes/` blueprint (or a new one registered in `routes/__init__.py`) rather than growing `app.py`.

**Backend routes** (all `GET /api/...`):
- `/r/<sub>` feed, `/r/<sub>/about`, `/r/<sub>/rules`
- `/r/<sub>/comments/<id>`, `/r/<sub>/morechildren/<id>`, `/r/<sub>/duplicates/<id>`
- `/r/<sub>/wiki`, `/r/<sub>/wiki/<page>`
- `/search`, `/search/communities`, `/search/users`, `/subreddit-search`
- `/user/<u>/about`, `/user/<u>/posts`, `/user/<u>/comments`, `/user/<u>/overview`, `/user/<u>/m/<multi>`
- `/redgifs/<id>`, `/redgifs/batch`, `/redgifs/media/<filename>` (CORS proxy)
- `/m/<host>/<path>` (media proxy for Reddit/Imgur/Giphy CDNs; with `PROXY_MEDIA=1` an after-request hook rewrites those URLs in JSON/SPA HTML to point here)
- `/img` (image proxy), `/resolve` (redirect follower), `/download`, `/download/gallery`, `/download/reddit-video`
- `/imgur/album/<id>`, `/live/<id>`, `/live/<id>/updates`, `/translate`, `/og-image`
- `GET /healthz` (not under `/api`): JSON liveness probe, 503 when degraded
- `GET /stats` (not under `/api`, `routes/stats.py`): owner-only operating stats page (`?format=json` for JSON), fed by the in-process counters in `core/stats.py`
- Atom feeds (not under `/api`, `routes/rss.py`): `/r/<sub>.rss`, `/r/<sub>/<sort>.rss`, `/user/<u>.rss`
- Page routes (`routes/pages.py`) render `index.html` for every SPA URL; `/r/...json`-style Reddit URLs are proxied by `routes/passthrough.py`

**Shared fetchers:** each `routes/` module exposes `fetch_*` functions (e.g. `subreddit.fetch_feed`, `users.fetch_user_overview`) that return plain data and raise `core.helpers.UpstreamError` on failure. Both the `/api/*` endpoints and the page routes call them — add new Reddit calls as a `fetch_*` function rather than inline in an endpoint, so the no-JS page can reuse it.

**Server-rendered pages / no-JS fallback:** every page route gets its data from a `build_*` function in `routes/page_data.py`, which returns both the JS hydration payload (`initial_data`/`initial_about`/`initial_post`/`initial_profile`, injected as `window.__INITIAL_*__`; `feed.js`/`postview.js`/`profile.js` check their `_`-prefixed keys before using it) and the noscript view (`ns_view` + `page`). The noscript UI lives in `templates/noscript/`: `layout.html` (header) includes `<ns_view>.html`, with shared macros in `macros/`. Its markup reuses the JS app's CSS classes. JS-driven controls become plain links/GET forms, and click-to-toggle becomes `<details>`. Visitor settings are cookies (`ns_prefs.PREFS`, form at `/settings`). When adding a JS feature, add its noscript equivalent too (or say why it can't work without JS).

**Frontend modules** (`static/`, bundled from `app.js`):
- Core: `app.js` (entry), `router.js`, `state.js`, `nav.js` (route-change hooks for header/menu chrome), `links.js` (link interception), `hydration.js` (single-use server-rendered first-load payloads), `storage.js` (localStorage wrappers that never throw), `csrf.js`, `utils.js`.
- Views: `feed.js`, `postview.js`, `profile.js`, `search.js`, `wiki.js`, `live.js`, `account.js`, `sidebar.js`.
- Feed plumbing: `pagination.js` (infinite scroll), `home-pagination.js`, `homefeed-seen.js` (home-feed history per source/account), `home-mode.js`, `hibernate.js` (unloads far-off-screen cards), `refresh.js` (retry), `sortbar.js`, `feeds-menu.js`.
- Rendering and media: `render.js` (posts, comments, `renderMd`), `media.js` (HLS, audio sync, GIF videos), `media-lifecycle.js` (releases players/observers with their DOM node), `lightbox.js`, `translate.js`.
- Local data: `settings.js`, `settings-panel.js`, `visited.js`, `visited-ui.js`, `saved.js`, `subscriptions.js`, `subscribe-btn.js`, `filters.js`, `backup.js`.
- Logged-in account: `vote.js`, `comment.js`, `submit.js`.
- Input: `keyboard.js`, `autocomplete.js`, `interactions.js` (header/bottom-nav shortcuts), `search-box.js`.

Styles: `style.css` (dark theme, flexbox/grid, no framework).

**Key patterns:**
- `feedGen` counter — discard stale async responses on navigation
- Event delegation on `#feed` and `#post-view`
- `mediaHtmlCard()` / `mediaHtmlFull()` for card vs post-view media
- HLS via `setupHls()` (lazy load); reddit videos have separate audio synced via `syncAudio()`; videos use native browser controls
- `renderMd()` uses `marked` + `DOMPurify`; handles spoilers, `r/sub`/`u/user` mentions, forces new-tab links
- Comment tree recursive to `THREAD_MAX_DEPTH = 4`
- Settings in `localStorage` via `settings.js`; `applySettings()` toggles body CSS classes
- `IntersectionObserver` pauses off-screen GIF `<video>` elements
- `visited.js`: `markVisited()` / `isVisited()` / `clearVisited()`, capped at 2000 IDs

## After Making a Change

- If any `.js` module was edited, rebuild the bundle before committing: `npx esbuild static/app.js --bundle --minify --outfile=static/app.bundle.js`
- If `style.css` was edited, rebuild the minified copy before committing: `npx esbuild static/style.css --minify --outfile=static/style.min.css` — `templates/index.html` links `style.min.css`, not `style.css`, so edits are invisible until this runs
