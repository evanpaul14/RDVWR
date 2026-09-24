FROM python:3.12-slim

# ffmpeg is needed by /api/download/reddit-video and /api/v/<id>.mp4 to merge video + audio
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py helpers.py shreddit.py archive.py media_detection.py reddit_client.py ./
COPY routes/ routes/
COPY static/ static/
COPY templates/ templates/

ENV PYTHONUNBUFFERED=1
EXPOSE 8002

# WEB_CONCURRENCY defaults to 1 worker (threads handle concurrency within it). Each
# worker keeps its own OAuth device pool and, without REDIS_URL set, its own
# in-process cache/rate-limit state — so raising this past 1 only makes sense once
# REDIS_URL points at a shared Redis instance (see helpers.py).
ENV WEB_CONCURRENCY=1
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:8002 --workers ${WEB_CONCURRENCY} --threads 16 --timeout 200 app:app"]
