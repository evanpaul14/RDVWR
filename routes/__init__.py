"""Flask blueprints, one per feature area. register_all() wires them onto the app.
routes/page_data.py isn't a blueprint: it builds the data for the page routes in pages.py."""
from routes import (media, downloads, search, subreddit, home, comments, avatars,
                    users, live, embeds, pages, mediaproxy, ns_settings, passthrough,
                    muxvideo)


def register_all(app):
    for mod in (media, downloads, search, subreddit, home, comments, avatars,
                users, live, embeds, muxvideo, ns_settings, pages, mediaproxy, passthrough):
        app.register_blueprint(mod.bp)
