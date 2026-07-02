# RDVWR

![RDVWR UI](https://i.imgur.com/3NwifGP.png)

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
- Media support: galleries, Reddit video (HLS + audio sync), YouTube, GIFs etc.
- Dark theme, mobile-friendly

## Stack

- **Backend:** Python/Flask — proxies Reddit's public JSON API
- **Frontend:** Vanilla JS with client-side routing, `marked` + `DOMPurify` for markdown, `hls.js` for video

### Credit

- [Redlib](https://github.com/redlib-org/redlib) for the Reddit OAuth spoofing logic
