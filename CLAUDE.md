# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
pip install -r requirements.txt
python app.py          # dev server on http://localhost:8002
```

## Architecture

A minimal Reddit viewer (RDVWR) — a single-page app with a Python/Flask backend and a self-contained frontend.

**Backend (`app.py`):** Flask proxies Reddit's JSON API (unauthenticated, no OAuth). All Reddit data flows through `process_post()`, which normalizes raw Reddit post dicts into a consistent shape (handling galleries, reddit-hosted video, HLS streams, YouTube embeds, redgifs embeds, and preview images). Five route groups: SPA catch-all, `/api/search`, `/api/r/<sub>`, `/api/r/<sub>/comments/<id>`, and `/api/user/<username>`.

**Frontend (`templates/index.html`):** Single HTML file — all CSS and JS inline, no build step. The JS implements a client-side router using `history.pushState`; `parseRoute()` maps URL patterns to route types (`home`, `sub`, `post`, `user`, `search`), and `renderRoute()` dispatches to the appropriate load function. Feed state (current sub, sort, pagination cursor, profile/search mode) is held in module-level variables. CDN dependencies: `marked` + `DOMPurify` for markdown rendering, `hls.js` for HLS video.

**Key data flow:** Search input → if prefixed `r/` routes to subreddit, otherwise triggers search. Post cards use event delegation on `#feed`. Reddit links in rendered markdown are intercepted and routed in-app. The sort bar is reused for subreddit sort tabs, profile tabs (Posts/Comments), and search sort tabs depending on mode.
