# RDVWR

A minimal Reddit viewer. Browse subreddits, posts, comments, user profiles, and search — no Reddit account required.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Opens at `http://localhost:8002`.

## Features

- Subreddit feeds with sort/time filters
- Post view with nested comment threads
- User profiles (posts + comments)
- Subreddit and post search
- Media support: galleries, Reddit video (HLS + audio sync), RedGifs, YouTube, GIFs
- Dark theme, mobile-friendly

## Stack

- **Backend:** Python/Flask — proxies Reddit's public JSON API
- **Frontend:** Vanilla JS with client-side routing, `marked` + `DOMPurify` for markdown, `hls.js` for video
