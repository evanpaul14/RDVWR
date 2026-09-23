# CLAUDE.md

## Running

```bash
pip install -r requirements.txt
python app.py          # http://localhost:8002
python3 -m pytest tests/
```

Tests: `test_media_detection.py`, `test_routes.py`, `test_oauth_device.py`.

Do not install or run Playwright (or any other browser-automation tool) to verify frontend changes unless explicitly asked to — ask first.

## Architecture

Single-page Reddit viewer. Python/Flask backend proxies Reddit API; ES module frontend.

**Backend files:** `app.py` (Flask app setup + blueprint registration only), `helpers.py` (shared constants, allowlist regexes, `TTLCache`, `server_cache`/`cached_json`/`validate_params`, `hydrate_linked_posts`), `routes/` (one blueprint per area: `media`, `downloads`, `search`, `subreddit`, `home`, `comments`, `avatars`, `users`, `live`, `embeds`, `pages`, `mediaproxy` — registered in `routes/__init__.py`), `shreddit.py` (shreddit HTML post parsers), `archive.py` (Arctic Shift fallback for user profiles), `media_detection.py` (media helpers), `reddit_client.py` (OAuth via Android device-token spoofing — rotating pool of 3 identities, tokens refresh every 30 min; disable with `REDDIT_OAUTH=0`), `update_vendor.py` (update vendored JS — run manually).

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
- `/imgur/album/<id>`, `/live/<id>`, `/live/<id>/updates`, `/translate`, `/og-image`
- SPA catch-all renders `index.html`

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
