# RDVWR

> A minimal Reddit viewer

![RDVWR UI](https://i.imgur.com/3NwifGP.png)

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
   - [Media proxying](#media-proxying)
5. [Credit](#credit)

---

# About

RDVWR is a single-page Reddit viewer. A Python/Flask backend proxies Reddit's API, and a vanilla JS frontend handles routing and rendering in the browser.

## Features

- Subreddit feeds with sort/time filters
- Post view with nested comment threads
- User profiles (posts + comments)
- Subreddit and post search
- Media support: galleries, Reddit video (HLS + audio sync), YouTube, GIFs etc.
- Dark theme, mobile-friendly

## Built with

- **Backend:** [Python](https://www.python.org/) / [Flask](https://flask.palletsprojects.com/) — proxies Reddit's public JSON API
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

> [!NOTE]
> rdvwr was served from `localhost:8002`; reddit.com over the open internet, so this isn't apples-to-apples on network latency — but it reflects the real difference a user feels, driven mostly by rdvwr shipping far less JS/CSS/image weight per page.

---

# Deployment

## Docker

### Docker Compose

```bash
git clone https://github.com/evanpaul14/RDVWR.git
cd RDVWR
docker compose up -d --build
```

Serves on port 8002. Set any of the [environment variables](#environment-variables) in the environment (or a `.env` file) to pass them through.

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
| `PROXY_MEDIA` | `0` | Set to `1` to route media through the server (see below). |
| `DISABLE_NSFW` | `0` | Set to `1` to strip over-18 posts/communities from every response and 404 direct links to them. |
| `DISABLE_DOWNLOADS` | `0` | Set to `1` to 403 the `/api/download*` endpoints (including the ffmpeg-based reddit-video merge). |
| `REDIS_URL` | _(unset)_ | Backs the response cache with Redis instead of in-process state, so it stays correct across multiple `WEB_CONCURRENCY` workers or horizontally-scaled instances. Leave unset for a single-instance deployment. |
| `WEB_CONCURRENCY` | `1` | gunicorn worker count. Only raise this once `REDIS_URL` is set — otherwise each worker has its own cache. |

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
| `RDVWR_DEFAULT_HOME_FEED` | `personalized` | `personalized`, `subscribed` |
| `RDVWR_DEFAULT_PAGINATION` | `0` | `1` disables infinite scroll in favor of a "load more" button |
| `RDVWR_DEFAULT_SHOW_AVATARS` | `0` | `1` shows profile pictures |
| `RDVWR_DEFAULT_LINK_EXTERNAL_MEDIA` | `0` | `1` links out to third-party media instead of embedding it |
| `RDVWR_DEFAULT_NSFW_BLUR` | `0` | `1` blurs NSFW thumbnails |
| `RDVWR_DEFAULT_NSFW_HIDE` | `0` | `1` hides NSFW posts |
| `RDVWR_DEFAULT_NSFW_SEARCH_HIDE` | `0` | `1` hides NSFW content in search |
| `RDVWR_DEFAULT_MARK_READ` | `1` | `0` stops marking posts as read on scroll |
| `RDVWR_DEFAULT_HIDE_READ_HOME` *(experimental)* | `0` | `1` hides read posts on the home feed. No settings-panel toggle; set via env var only. |
| `RDVWR_DEFAULT_HIDE_READ_SUB` *(experimental)* | `0` | `1` hides read posts in subreddits. No settings-panel toggle; set via env var only. |

## Media proxying

`PROXY_MEDIA=1` routes images, videos and gifs from Reddit, Imgur and Giphy through the server (`/api/m/`), so the browser never contacts those CDNs directly.

> [!WARNING]
> All media bandwidth then flows through the server.

## Rate limiting

The app itself does no client-facing rate limiting — put a reverse proxy in front of it for that (same approach as [Redlib](https://github.com/redlib-org/redlib)). Example nginx config, tighter on the ffmpeg-based video download:

```nginx
limit_req_zone $binary_remote_addr zone=rdvwr_general:10m rate=60r/m;
limit_req_zone $binary_remote_addr zone=rdvwr_download:10m rate=6r/m;

server {
    location /api/download/ {
        limit_req zone=rdvwr_download burst=3 nodelay;
        proxy_pass http://127.0.0.1:8002;
    }
    location / {
        limit_req zone=rdvwr_general burst=20 nodelay;
        proxy_pass http://127.0.0.1:8002;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

# Credit

- [Redlib](https://github.com/redlib-org/redlib) for the Reddit OAuth spoofing logic
