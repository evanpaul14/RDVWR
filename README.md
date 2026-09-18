# RDVWR

![RDVWR UI](https://i.imgur.com/3NwifGP.png)

A minimal Reddit viewer. Browse subreddits, posts, comments, user profiles, and search — no Reddit account required.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Opens at `http://localhost:8002`.

### Docker

```bash
docker compose up -d --build
```

Serves on port 8002. Set `REDDIT_OAUTH=0`, `IMGUR_CLIENT_ID` or `PROXY_MEDIA=1` in the environment (or a `.env` file) to pass them through.

`PROXY_MEDIA=1` routes images, videos and gifs from Reddit, Imgur and Giphy through the server (`/api/m/`), so the browser never contacts those CDNs directly. All media bandwidth then flows through the server.

## Features

- Subreddit feeds with sort/time filters
- Post view with nested comment threads
- User profiles (posts + comments)
- Subreddit and post search
- Media support: galleries, Reddit video (HLS + audio sync), YouTube, GIFs etc.
- Dark theme, mobile-friendly

## Performance

Measured with Playwright (Firefox), fresh browser context per run, median of 3 runs. rdvwr served from `localhost:8002`; reddit.com over the open internet, so this isn't apples-to-apples on network latency — but it reflects the real difference a user feels, driven mostly by rdvwr shipping far less JS/CSS/image weight per page.

| Page | Metric | rdvwr | reddit.com |
|---|---|---:|---:|
| Home | Wall load time | 1087 ms | 4278 ms |
| Home | Data transferred | 182 KB | 5914 KB |
| Home | Requests | 10 | 104 |
| Feed (r/pics) | Wall load time | 1038 ms | 3371 ms |
| Feed (r/pics) | Data transferred | 188 KB | 1721 KB |
| Feed (r/pics) | Requests | 10 | 57 |

## Stack

- **Backend:** Python/Flask — proxies Reddit's public JSON API
- **Frontend:** Vanilla JS with client-side routing, `marked` + `DOMPurify` for markdown, `hls.js` for video

### Credit

- [Redlib](https://github.com/redlib-org/redlib) for the Reddit OAuth spoofing logic
