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
| `IMGUR_CLIENT_ID` | _(empty)_ | Imgur API client ID, used to fetch Imgur album contents. |
| `PROXY_MEDIA` | `0` | Set to `1` to route media through the server (see below). |

## Media proxying

`PROXY_MEDIA=1` routes images, videos and gifs from Reddit, Imgur and Giphy through the server (`/api/m/`), so the browser never contacts those CDNs directly.

> [!WARNING]
> All media bandwidth then flows through the server.

---

# Credit

- [Redlib](https://github.com/redlib-org/redlib) for the Reddit OAuth spoofing logic
