# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
pip install -r requirements.txt
python app.py          # dev server on http://localhost:8002
```

No build step, linter, or test suite exists in this project.

## Architecture

A minimal Reddit viewer (RDVWR) — a single-page app with a Python/Flask backend and a self-contained frontend split across three files: `app.py`, `static/app.js`, and `static/style.css` (loaded via `templates/index.html`).

**Backend (`app.py`):** Flask proxies Reddit's JSON API (unauthenticated, no OAuth). All Reddit post data flows through `process_post()`, which normalizes raw Reddit post dicts into a consistent shape. It detects and extracts media in priority order: galleries → redgifs → reddit-hosted video (HLS + audio) → reddit_video_preview → YouTube → generic iframe embed → GIF/GIFV. RedGifs requires a temporary token (`get_redgifs_token()`) and proxies media through `/api/redgifs/media/<filename>` to work around CORS.

Backend routes:
- `GET /api/r/<sub>` — subreddit feed (sort, t, after)
- `GET /api/r/<sub>/about` — subreddit metadata for ctx panel
- `GET /api/r/<sub>/rules` — sidebar rules
- `GET /api/r/<sub>/comments/<id>` — post + nested comment tree (sort, comment, context)
- `GET /api/search` — post search (q, sort, t, sub, nsfw)
- `GET /api/search/communities` — community search
- `GET /api/search/users` — user search
- `GET /api/subreddit-search` — autocomplete (min 2 chars, returns up to 8 names)
- `GET /api/user/<username>/about` — user profile metadata
- `GET /api/user/<username>/posts` — user submitted posts
- `GET /api/user/<username>/comments` — user comments
- `GET /api/redgifs/<gif_id>` — resolves hd/sd URLs via RedGifs API
- `GET /api/redgifs/media/<filename>` — streaming proxy for RedGifs mp4s
- SPA catch-all renders `index.html` for `/`, `/r/*`, `/user/*`, `/u/*`, `/search`

**Frontend (`static/app.js`):** Client-side router using `history.pushState`. `parseRoute()` maps URL patterns to route objects (`home`, `sub`, `post`, `user`, `search`); `renderRoute()` dispatches to `loadSubreddit()`, `loadPostView()`, `loadProfile()`, or `loadSearch()`. Feed state (current sub, sort, time, pagination `afterToken`, profile/search mode variables) lives in module-level variables — there is no state management library.

Key frontend patterns:
- **Feed generation counter (`feedGen`):** Incremented on each new feed load; async callbacks check `myGen !== feedGen` to discard stale responses when the user navigates before a fetch completes.
- **Event delegation:** `#feed` and `#post-view` use single delegated click handlers rather than per-card listeners.
- **Sort bar reuse:** The same `#sort-bar` element is repopulated with different HTML (`buildSubSortHtml`, `buildProfileSortHtml`, `SEARCH_SORT_BTN_HTML`) depending on the current mode.
- **Post layout:** Link posts without rich media render in a compact two-column layout; self posts and media posts use a full-width layout. `mediaHtmlCard()` vs `mediaHtmlFull()` produce card vs post-view variants.
- **HLS video:** `setupHls()` uses hls.js with lazy `startLoad` on first play. Reddit's `v.redd.it` videos are video-only; audio is synced via a separate `<audio>` element in `syncAudio()`.
- **Markdown:** `renderMd()` uses `marked` + `DOMPurify` with a custom renderer that handles Reddit's spoiler syntax (`>!...!<`), bare `r/sub` and `u/user` mentions (via `linkifyReddit()`), giphy/redgifs image tokens, and forces all links to open in a new tab.
- **Comment tree:** Rendered recursively up to `THREAD_MAX_DEPTH = 4`; deeper threads show a "Continue thread →" link instead.

**`static/style.css`:** Dark-theme CSS vars under `:root`. No framework — all layout uses flexbox/grid.
