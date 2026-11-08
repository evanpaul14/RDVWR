# RDVWR

> A minimal Reddit viewer

![RDVWR UI](https://files.catbox.moe/gh1ne8.png)

**10-second pitch:** RDVWR is a lightweight front-end for Reddit. Browse subreddits, posts, comments, user profiles, and search — no Reddit account required.

- 🚀 Fast: a fraction of the page weight and requests of reddit.com
- 🔓 No Reddit account required
- 🖼️ Media support: galleries, Reddit video (HLS + audio sync), YouTube, GIFs etc.
- 🌙 Dark theme, mobile-friendly

---

## Table of Contents

1. [About](#about)
   - [Features](#features)
   - [Built with](#built-with)
2. [Comparison](#comparison)
   - [Speed](#speed)
3. [Deployment](#deployment)
   - [Docker](#docker)
   - [Running from source](#running-from-source)
4. [Configuration](#configuration)
   - [Environment variables](#environment-variables)
   - [Default settings](#default-settings)
   - [Reddit login](#reddit-login)
   - [Media proxying](#media-proxying)
   - [Health check](#health-check)
5. [Credit](#credit)

---

# About

RDVWR is a single-page Reddit viewer. A Python/Flask backend proxies Reddit's API, and a vanilla JS frontend handles routing and rendering in the browser.

## Features

- Subreddit, multireddit and home feeds with sort/time filters
- Post view with nested comment threads, duplicates, wiki pages and live threads
- User profiles (posts + comments)
- Subreddit, user and post search
- Local saved posts and subscriptions, plus read tracking (all stored in your browser)
- Optional Reddit login for voting, commenting and posting (see [Reddit login](#reddit-login))
- Media support: galleries, Reddit video (HLS + audio sync), YouTube, GIFs etc.
- Dark theme, mobile-friendly
- Works without JavaScript: every page is also server-rendered, with its own cookie-based settings page (`/settings`)

The no-JavaScript settings support themes, all three layouts, feed/comment defaults,
the logged-in Home feed choice, server-fetched comment pictures, awards, media privacy,
and content filters, with a reset-to-defaults button. They are independent of the JS
app's browser settings. Navigation uses links/forms, comment and spoiler toggles use
native disclosures, and feeds use previous/next links. Profile details and trophies,
account actions and media downloads are also available. Comment pictures use the
backend's bounded initial batch; additional pictures need the JS client's lazy loading.
Browser-local saved posts, local subscriptions, read history, clipboard actions,
keyboard shortcuts and custom media controls require JavaScript. Native Reddit video
plays with sound in browsers that support HLS; other browsers get the MP4 fallback.

## Built with

- **Backend:** [Python](https://www.python.org/) / [Flask](https://flask.palletsprojects.com/) — proxies Reddit's API (spoofed OAuth, public JSON, and the app's GraphQL as a fallback when Reddit blocks REST)
- **Frontend:** Vanilla JS with client-side routing
- [marked](https://github.com/markedjs/marked) + [DOMPurify](https://github.com/cure53/DOMPurify) for markdown
- [hls.js](https://github.com/video-dev/hls.js) for video

---

# Comparison

## Speed

Measured with Playwright (Firefox), fresh browser context per run, median of 3 runs.

| Page | Metric | rdvwr | reddit.com |
|---|---|---:|---:|
| Home | Wall load time | 1087 ms | 4278 ms |
| Home | Data transferred | 182 KB | 5914 KB |
| Home | Requests | 10 | 104 |
| Feed (r/pics) | Wall load time | 1038 ms | 3371 ms |
| Feed (r/pics) | Data transferred | 188 KB | 1721 KB |
| Feed (r/pics) | Requests | 10 | 57 |

---

# Deployment

## Docker

### Docker Compose

```bash
git clone https://github.com/evanpaul14/RDVWR.git
cd RDVWR
docker compose up -d --build
```

Serves on port 8002. Set any of the [environment variables](#environment-variables) in the environment (or a `.env` file) to pass them through. Reddit login cookies are kept in the `rdvwr-data` volume so they survive rebuilds.

## Running from source

```bash
git clone https://github.com/evanpaul14/RDVWR.git
cd RDVWR
pip install -r requirements.txt
python app.py
```

Opens at `http://localhost:8002`.

Run the tests with:

```bash
python3 -m pytest tests/
```

---

# Configuration

## Environment variables

| Name | Default | Description |
|---|---|---|
| `REDDIT_OAUTH` | `1` | Spoofs Reddit OAuth. Set `0` to disable and use public JSON API. |
| `GITHUB_DISPATCH_TOKEN` | _(unset)_ | Fine-grained GitHub token (Actions read/write on the repo). When comment loading fails, the server uses it to run the `update-gql-hash` workflow right away instead of waiting for its daily run. |
| `BASE_PATH` | _(unset)_ | Serve the app under a URL prefix, e.g. `/rdvwr`, behind a reverse proxy that strips it (such as Caddy's `handle_path`). Routes stay root-relative; only the URLs the app emits are prefixed. |
| `PROXY_MEDIA` | `0` | Set to `1` to route media through the server (see below). |
| `DISABLE_NSFW` | `0` | Set to `1` to strip over-18 posts/communities from every response and 404 direct links to them. |
| `RDVWR_ENABLE_NSFW_ONLY_SEARCH` | `0` | Set to `1` to offer an "NSFW only" checkbox in search (next to the "limit to r/sub" one) that limits results to over-18 posts. Adds a matching toggle to the settings panel, on by default; hidden while this is `0` or `DISABLE_NSFW=1`. The checkbox is also hidden when the visitor hides NSFW posts. |
| `DISABLE_DOWNLOADS` | `0` | Set to `1` to 403 the `/api/download*` endpoints (including the ffmpeg-based reddit-video merge). |
| `RDVWR_DISABLE_PERSONALIZED_HOME` | `0` | Set to `1` to disable the personalized (logged-in) home feed entirely. |
| `REDDIT_LOGIN` | `0` | Set to `1` to enable logging in to a Reddit account from the settings panel (see [Reddit login](#reddit-login)). |
| `REDDIT_LOGIN_FILE` | `.reddit_login.json` next to the app | Where the stored Reddit login cookies are saved. The Docker image sets `/data/.reddit_login.json`. |
| `REDDIT_LOGIN_OWNER_KEY` | _(unset)_ | With `REDDIT_LOGIN=1`, a long random passphrase that lets you use the logged-in account from other devices behind a reverse proxy: enter it once per browser at `/auth/owner`. Serve over HTTPS. |
| `REDIS_URL` | _(unset)_ | Backs the response cache with Redis instead of in-process state, so it stays correct across multiple `WEB_CONCURRENCY` workers or horizontally-scaled instances. Leave unset for a single-instance deployment. |
| `WEB_CONCURRENCY` | `1` | gunicorn worker count. Only raise this once `REDIS_URL` is set — otherwise each worker has its own cache. |
| `ALL_PROXY` | _(unset)_ | Forward proxy for all outbound Reddit/media requests, e.g. `socks5h://127.0.0.1:9050` or `http://user:pass@host:port`. Sets both `HTTP_PROXY` and `HTTPS_PROXY` at once (same convention as [Redlib](https://github.com/redlib-org/redlib)). |
| `HTTP_PROXY` / `HTTPS_PROXY` | _(unset)_ | Same as `ALL_PROXY` but scoped to one scheme. `http://`, `https://`, `socks4://`, `socks4a://`, `socks5://`, and `socks5h://` proxy URLs are all supported. |

### Default settings

These set the out-of-the-box value for each toggle in the settings panel. A visitor changing a
setting always overrides these — they're only the starting point (stored in their browser via
`localStorage`, same as [Redlib's `REDLIB_DEFAULT_*` vars](https://github.com/redlib-org/redlib)).

| Name | Default | Allowed values |
|---|---|---|
| `RDVWR_DEFAULT_THEME` | `dark` | `dark`, `light`, `system` |
| `RDVWR_DEFAULT_LAYOUT` | `card` | `card`, `compact`, `minimal` |
| `RDVWR_DEFAULT_SUB_SORT` | `hot` | `hot`, `new`, `top`, `rising`, `controversial` |
| `RDVWR_DEFAULT_SUB_TIME` | `day` | `hour`, `day`, `week`, `month`, `year`, `all` |
| `RDVWR_DEFAULT_COMMENT_SORT` | `confidence` | `confidence`, `top`, `new`, `controversial`, `old`, `qa` |
| `RDVWR_DEFAULT_HOME_FEED` | `personalized` | `personalized`, `subscribed`, `popular` |
| `RDVWR_DEFAULT_PERSONALIZED_HOME` | `1` | `0` turns the personalized home feed off by default (it only applies with pasted cookies or a [Reddit login](#reddit-login)) |
| `RDVWR_DEFAULT_PAGINATION` | `0` | `1` disables infinite scroll in favor of a "load more" button |
| `RDVWR_DEFAULT_SHOW_AVATARS` | `0` | `1` shows profile pictures |
| `RDVWR_DEFAULT_SHOW_AWARDS` | `1` | `0` hides award icons on posts and comments |
| `RDVWR_DEFAULT_LINK_EXTERNAL_MEDIA` | `0` | `1` links out to third-party media instead of embedding it |
| `RDVWR_DEFAULT_NSFW_BLUR` | `0` | `1` blurs NSFW thumbnails |
| `RDVWR_DEFAULT_NSFW_HIDE` | `0` | `1` hides NSFW posts |
| `RDVWR_DEFAULT_NSFW_SEARCH_HIDE` | `0` | `1` hides NSFW content in search |
| `RDVWR_DEFAULT_MARK_READ` | `1` | `0` stops marking posts as read on scroll |
| `RDVWR_DEFAULT_HIDE_READ_HOME` *(experimental)* | `0` | `1` hides read posts on the home feed. No settings-panel toggle; set via env var only. |
| `RDVWR_DEFAULT_HIDE_READ_SUB` *(experimental)* | `0` | `1` hides read posts in subreddits. No settings-panel toggle; set via env var only. |

## Reddit login

Set `REDDIT_LOGIN=1` to add a "Reddit account" section to settings. Reddit no longer issues API apps, so there's no OAuth: sign in on reddit.com in your own browser, then paste the session cookies into the form (F12 → Application → Cookies → right-click the `reddit.com` row → Copy all as header value). The server checks them against Reddit and stores them.

**What you get (JS app and no-JS pages):**

- Personalized Home feed, and your vote shown on posts
- Up/down vote arrows on posts and comments
- Join/leave on the real subreddit; the Subscribed feed (`/subscribed`) becomes your account's own front page, with no 50-subreddit cap
- Comment box and reply buttons in the post view
- Create posts from the header "+" button or `/submit`: text, link, image, gallery and video, with optional flair
- Comments can carry one image or GIF
- `/account`: your own posts and comments (edit/delete), profile picture, sign out

**Limits and requirements:**

- Uploads go straight from the server to Reddit: images/GIFs up to 20 MB, videos up to 100 MB. They are held in memory, so mind the size on a small machine.
- Video posts need `ffmpeg` on the server for the poster frame Reddit requires.
- A comment with an image is sent as rich text, so markdown formatting in that comment isn't applied.
- Needs a writable disk, so it won't work on Vercel.

**Security:**

- Cookies go only to Reddit and are never sent back to the browser.
- They're stored in `.reddit_login.json` (mode 0600; override the path with `REDDIT_LOGIN_FILE`). Logging out deletes the file.
- These controls never appear on the public site: login and account routes only answer requests coming directly from the server's own machine, so use `localhost` or an SSH tunnel.
- To use the account from other devices behind a reverse proxy, set `REDDIT_LOGIN_OWNER_KEY` (see [Environment variables](#environment-variables)).

## Media proxying

`PROXY_MEDIA=1` routes images, videos and gifs from Reddit, Imgur and Giphy through the server (`/api/m/`), so the browser never contacts those CDNs directly.

> [!WARNING]
> All media bandwidth then flows through the server.

## Health check

`GET /healthz` returns JSON for uptime monitors and load balancers, with no identities, tokens or cookies in it:

```json
{"status": "ok",
 "oauth": {"enabled": true, "pool_size": 3, "tokens_valid": 3, "ever_acquired": true},
 "gql": {"hashes": 13, "last_github_check_secs_ago": null},
 "cache": {"backend": "memory", "ok": true}}
```

It answers `200` normally and `503` with `"status": "degraded"` when the Redis cache is unreachable, or when OAuth has issued tokens before but none are currently valid. Tokens are minted on demand, so a freshly started server with none yet is still `ok`.

---

# Credit

- [Redlib](https://github.com/redlib-org/redlib) for the Reddit OAuth spoofing logic and feature inspiration
