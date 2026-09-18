FROM python:3.12-slim

# ffmpeg is needed by /api/download/reddit-video to merge video + audio
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py media_detection.py reddit_client.py ./
COPY static/ static/
COPY templates/ templates/

ENV PYTHONUNBUFFERED=1
EXPOSE 8002

# Single worker so the OAuth token pool in reddit_client is shared; threads for concurrency
CMD ["gunicorn", "--bind", "0.0.0.0:8002", "--workers", "1", "--threads", "16", "--timeout", "200", "app:app"]
