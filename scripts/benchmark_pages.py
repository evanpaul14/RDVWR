#!/usr/bin/env python3
"""Compare real feeds in visible Chrome; save every sample, including failures.

Requires Playwright and an installed Chrome browser. A temporary Chrome profile
keeps anonymous verification cookies across runs; the HTTP cache is disabled.
"""
import argparse
import json
import platform
import shutil
import socket
import statistics
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright


POST_TIMING = """() => {
  window.__firstPostMs = null;
  function check() {
    const post = document.querySelector('.post[data-post-id], shreddit-post');
    if (post && post.getBoundingClientRect().height > 0) {
      window.__firstPostMs = performance.now();
    } else { requestAnimationFrame(check); }
  }
  requestAnimationFrame(check);
}"""


def measure(context, url, window_seconds):
    page = context.new_page()
    page.set_viewport_size({"width": 1365, "height": 900})
    page.add_init_script(f"({POST_TIMING})();")
    cdp = context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    cdp.send("Network.setBypassServiceWorker", {"bypass": True})
    traffic = {"requests": 0, "completed_response_bytes": 0,
               "finished_requests": 0, "failed_requests": 0, "http_errors": 0}

    def requested(_):
        traffic["requests"] += 1

    def finished(event):
        traffic["completed_response_bytes"] += event["encodedDataLength"]
        traffic["finished_requests"] += 1

    def failed(_):
        traffic["failed_requests"] += 1

    def response(event):
        if event["response"]["status"] >= 400:
            traffic["http_errors"] += 1

    cdp.on("Network.requestWillBeSent", requested)
    cdp.on("Network.loadingFinished", finished)
    cdp.on("Network.loadingFailed", failed)
    cdp.on("Network.responseReceived", response)
    started = time.perf_counter()
    status, error = None, None
    try:
        navigation = page.goto(url, wait_until="commit", timeout=window_seconds * 1000)
        status = navigation.status if navigation else None
        remaining = window_seconds - (time.perf_counter() - started)
        if remaining > 0:
            page.wait_for_timeout(remaining * 1000)
        metrics = page.evaluate("""() => {
          const n = performance.getEntriesByType('navigation')[0];
          const fcp = performance.getEntriesByName('first-contentful-paint')[0];
          return {
            first_post_ms: window.__firstPostMs ?? null,
            dom_content_loaded_ms: n?.domContentLoadedEventEnd || null,
            load_event_ms: n?.loadEventEnd || null,
            first_contentful_paint_ms: fcp?.startTime ?? null,
            post_count: document.querySelectorAll('.post[data-post-id], shreddit-post').length,
            title: document.title
          };
        }""")
    except Exception as exc:
        metrics = {"first_post_ms": None, "post_count": 0}
        error = str(exc).splitlines()[0]
    result = {"url": url, "status": status, "observation_seconds": window_seconds,
              **metrics, **traffic, "error": error}
    result["valid"] = (status == 200 and metrics["post_count"] > 0
                       and metrics["first_post_ms"] is not None and error is None)
    page.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--reddit-url", default="https://www.reddit.com")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--window", type=float, default=10)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deployment", default="")
    parser.add_argument("--chrome", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    args = parser.parse_args()
    if args.runs < 1 or args.window <= 0:
        parser.error("runs and window must be positive")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    profile = tempfile.mkdtemp(prefix="rdvwr-benchmark-")
    chrome = subprocess.Popen([
        args.chrome, f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
        "--no-first-run", "--no-default-browser-check", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    data = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
            "client": platform.platform(), "deployment": args.deployment,
            "viewport": {"width": 1365, "height": 900}, "runs": args.runs,
            "window_seconds": args.window, "headless": False,
            "http_cache": "disabled", "cookies": "retained across visits",
            "warmup_rounds": 1, "samples": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(data, indent=2) + "\n")

    try:
        with sync_playwright() as p:
            browser = None
            for _ in range(100):
                try:
                    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    break
                except Exception:
                    time.sleep(0.1)
            if browser is None:
                raise RuntimeError("Chrome DevTools endpoint did not start")
            data["browser"] = "Chrome " + browser.version
            context = browser.contexts[0]
            # Allow manual verification before timing; never count a challenge page.
            setup = context.pages[0]
            setup.set_viewport_size(data["viewport"])
            setup.goto(args.reddit_url, wait_until="domcontentloaded", timeout=30000)
            print("Waiting for Reddit posts. Complete any verification in Chrome.", flush=True)
            setup.wait_for_selector("shreddit-post", state="visible", timeout=600000)
            print("Reddit feed verified. Starting benchmark; leave Chrome untouched.", flush=True)
            setup.close()
            sites = {"rdvwr": args.base_url.rstrip("/"),
                     "reddit": args.reddit_url.rstrip("/")}
            for round_index in range(args.runs + 1):
                order = ["rdvwr", "reddit"] if round_index % 2 == 0 else ["reddit", "rdvwr"]
                for page_name, path in [("home", "/"), ("pics", "/r/pics/")]:
                    for site in order:
                        sample = measure(context, sites[site] + path, args.window)
                        sample.update(site=site, page=page_name, round=round_index,
                                      warmup=round_index == 0)
                        data["samples"].append(sample)
                        save()
                        print(f"round={round_index} {site}/{page_name}: valid={sample['valid']} "
                              f"first_post_ms={sample['first_post_ms']} "
                              f"bytes={sample['completed_response_bytes']} "
                              f"requests={sample['requests']}", flush=True)
                        if not sample["valid"]:
                            raise RuntimeError(f"Feed did not load: {site}/{page_name}; see raw result")
            data["medians"] = {}
            for site in sites:
                data["medians"][site] = {}
                for page_name in ["home", "pics"]:
                    samples = [s for s in data["samples"] if s["site"] == site
                               and s["page"] == page_name and not s["warmup"] and s["valid"]]
                    data["medians"][site][page_name] = {
                        key: statistics.median(s[key] for s in samples)
                        if all(s.get(key) is not None for s in samples) else None
                        for key in ["first_post_ms", "dom_content_loaded_ms", "load_event_ms",
                                    "first_contentful_paint_ms", "completed_response_bytes", "requests"]}
            data["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
            save()
            print(json.dumps(data["medians"], indent=2), flush=True)
    finally:
        save()
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()
            chrome.wait()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
