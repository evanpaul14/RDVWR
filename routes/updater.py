"""Android-wrapper platform detection and over-the-air update endpoint."""
import os
import requests
from flask import Blueprint, jsonify

bp = Blueprint("updater", __name__)


@bp.route("/api/platform")
def get_platform():
    return jsonify({"android": bool(os.environ.get("RDVWR_UPDATE_DIR"))})


@bp.route("/api/update", methods=["POST"])
def do_update():
    import io, tarfile, hashlib
    update_dir = os.environ.get("RDVWR_UPDATE_DIR", "")
    if not update_dir:
        return jsonify({"status": "unsupported"})
    REPO = "evanpaul14/rdvwr-android"
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{REPO}/tarball/main",
            timeout=60,
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        return jsonify({"status": "error", "message": "No network connection"}), 503
    except requests.exceptions.Timeout:
        return jsonify({"status": "error", "message": "Request timed out"}), 503
    except requests.exceptions.RequestException as e:
        return jsonify({"status": "error", "message": str(e)}), 502
    try:
        buf = io.BytesIO(resp.content)
        changed = []
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            for member in tf.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                rel = parts[1]
                dest = os.path.join(update_dir, rel)
                if member.isdir():
                    os.makedirs(dest, exist_ok=True)
                elif member.isfile():
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    new_bytes = tf.extractfile(member).read()
                    try:
                        existing = open(dest, "rb").read()
                    except OSError:
                        existing = None
                    if existing != new_bytes:
                        with open(dest, "wb") as fh:
                            fh.write(new_bytes)
                        changed.append(rel)
        status = "updated" if changed else "up_to_date"
        return jsonify({"status": status, "changed": changed})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Extraction failed: {e}"}), 500
