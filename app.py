import os
import logging
from flask import Flask
from flask_compress import Compress
from helpers import CACHE_TTL_STATIC, rate_limit
from routes import register_all


app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = CACHE_TTL_STATIC
# Route Reddit/Imgur media through /api/m/ instead of letting the browser hit the CDNs.
app.config['PROXY_MEDIA'] = os.environ.get('PROXY_MEDIA', '0') == '1'
Compress(app)
app.before_request(rate_limit)


@app.context_processor
def _inject_asset_version():
    def asset_v(filename):
        try:
            return str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
        except OSError:
            return '0'
    return dict(asset_v=asset_v, proxy_media=app.config['PROXY_MEDIA'])


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("werkzeug").setLevel(logging.WARNING)


register_all(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, threaded=True)
