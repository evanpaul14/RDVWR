# CLAUDE.md

## Running

```bash
pip install -r requirements.txt
python app.py          # http://localhost:8002
python3 -m pytest tests/
```

Tests: `test_media_detection.py`, `test_routes.py`, `test_oauth_device.py`, `test_noscript.py` (server-rendered pages + no-JS fallback).

Do not install or run Playwright (or any other browser-automation tool) to verify frontend changes unless explicitly asked to — ask first.

## Architecture

Single-page Reddit viewer. Python/Flask backend proxies Reddit API; ES module frontend.

**Backend files:** `app.py` (Flask app setup + blueprint registration only), `helpers.py` (shared constants, allowlist regexes, `TTLCache`, `server_cache`/`cached_json`/`validate_params`, `hydrate_linked_posts`, `UpstreamError`), `routes/` (one blueprint per area: `media`, `downloads`, `search`, `subreddit`, `home`, `comments`, `avatars`, `users`, `live`, `embeds`, `muxvideo`, `pages`, `ns_settings`, `mediaproxy`, `passthrough` — registered in `routes/__init__.py`; plus `routes/page_data.py`, not a blueprint, which builds the data for `pages`), `template_helpers.py` (Jinja filters/globals), `ns_prefs.py` (noscript preference cookies), `reddit_html.py` (sanitizer for Reddit's `*_html` fields), `shreddit.py` (shreddit HTML post parsers), `archive.py` (Arctic Shift fallback for user profiles), `media_detection.py` (media helpers), `reddit_client.py` (OAuth via Android device-token spoofing — rotating pool of 3 identities, tokens refresh every 30 min; disable with `REDDIT_OAUTH=0`), `update_vendor.py` (update vendored JS — run manually).

`process_post()` normalizes post dicts. Media detection priority: galleries → redgifs → reddit HLS video → reddit_video_preview → YouTube → Streamable → TikTok → iframe → Imgur album → GIF/GIFV/Imgur image.

Keep each Python file under ~500 lines — add new endpoints to the matching `routes/` blueprint (or a new one registered in `routes/__init__.py`) rather than growing `app.py`.

**Backend routes** (all `GET /api/...`):
- `/r/<sub>` feed, `/r/<sub>/about`, `/r/<sub>/rules`, `/r/<sub>/about/moderators`
- `/r/<sub>/comments/<id>`, `/r/<sub>/morechildren/<id>`, `/r/<sub>/duplicates/<id>`
- `/r/<sub>/wiki`, `/r/<sub>/wiki/<page>`
- `/search`, `/search/communities`, `/search/users`, `/subreddit-search`
- `/user/<u>/about`, `/user/<u>/posts`, `/user/<u>/comments`, `/user/<u>/overview`, `/user/<u>/m/<multi>`
- `/redgifs/<id>`, `/redgifs/batch`, `/redgifs/media/<filename>` (CORS proxy)
- `/m/<host>/<path>` (media proxy for Reddit/Imgur/Giphy CDNs; with `PROXY_MEDIA=1` an after-request hook rewrites those URLs in JSON/SPA HTML to point here)
- `/img` (image proxy), `/resolve` (redirect follower), `/download`, `/download/gallery`, `/download/reddit-video`
- `/v/<id>.mp4` (ffmpeg remux of a v.redd.it HLS playlist into one MP4 with audio, disk-cached; used by the noscript view when sound is enabled)
- `/imgur/album/<id>`, `/live/<id>`, `/live/<id>/updates`, `/translate`, `/og-image`
- Page routes (`routes/pages.py`) render `index.html` for every SPA URL; `/r/...json`-style Reddit URLs are proxied by `routes/passthrough.py`

**Shared fetchers:** each `routes/` module exposes `fetch_*` functions (e.g. `subreddit.fetch_feed`, `users.fetch_user_overview`) that return plain data and raise `helpers.UpstreamError` on failure. Both the `/api/*` endpoints and the page routes call them — add new Reddit calls as a `fetch_*` function rather than inline in an endpoint, so the no-JS page can reuse it.

**Server-rendered pages / no-JS fallback:** every page route gets its data from a `build_*` function in `routes/page_data.py`, which returns both the JS hydration payload (`initial_data`/`initial_about`/`initial_post`/`initial_profile`, injected as `window.__INITIAL_*__`; `feed.js`/`postview.js`/`profile.js` check their `_`-prefixed keys before using it) and the noscript view (`ns_view` + `page`). The noscript UI lives in `templates/noscript/`: `layout.html` (header) includes `<ns_view>.html`, with shared macros in `macros/`. Its markup reuses the JS app's CSS classes. JS-driven controls become plain links/GET forms, and click-to-toggle becomes `<details>`. Visitor settings are cookies (`ns_prefs.PREFS`, form at `/settings`). When adding a JS feature, add its noscript equivalent too (or say why it can't work without JS).

**Frontend modules:** `app.js` (entry/router), `router.js`, `state.js`, `feed.js`, `postview.js`, `profile.js`, `search.js`, `wiki.js`, `live.js`, `sidebar.js`, `media.js`, `render.js`, `utils.js`, `settings.js`, `visited.js`, `visited-ui.js`, `keyboard.js`, `autocomplete.js`, `lightbox.js`, `player.js`. Styles: `style.css` (dark theme, flexbox/grid, no framework).

**Key patterns:**
- `feedGen` counter — discard stale async responses on navigation
- Event delegation on `#feed` and `#post-view`
- `mediaHtmlCard()` / `mediaHtmlFull()` for card vs post-view media
- HLS via `setupHls()` (lazy load); reddit videos have separate audio synced via `syncAudio()`; `initCustomPlayer()` adds custom controls overlay
- `renderMd()` uses `marked` + `DOMPurify`; handles spoilers, `r/sub`/`u/user` mentions, forces new-tab links
- Comment tree recursive to `THREAD_MAX_DEPTH = 4`
- Settings in `localStorage` via `settings.js`; `applySettings()` toggles body CSS classes
- `IntersectionObserver` pauses off-screen GIF `<video>` elements
- `visited.js`: `markVisited()` / `isVisited()` / `clearVisited()`, capped at 2000 IDs

## After Making a Change

- If any `.js` module was edited, rebuild the bundle before committing: `npx esbuild static/app.js --bundle --minify --outfile=static/app.bundle.js`
- If `style.css` was edited, rebuild the minified copy before committing: `npx esbuild static/style.css --minify --outfile=static/style.min.css` — `templates/index.html` links `style.min.css`, not `style.css`, so edits are invisible until this runs
- Commit and push — DO NOT mention redgifs, nsfw, 18+, or any adult content terms in commit messages
- `ssh mypi`, cd `/home/evanpaul/RDVWR`, run `git pull && sudo systemctl restart rdvwr`

There is also a Vercel project linked to this repo (`.vercel/project.json`, gitignored) separate from the Pi deployment above. No `vercel.json` is committed, so the platform auto-detects `app.py` and only `/api/*` paths are routed to it as a serverless function.
