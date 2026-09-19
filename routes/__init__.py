"""Flask blueprints, one per feature area. register_all() wires them onto the app."""
from routes import (media, downloads, search, subreddit, home, comments, avatars,
                    users, live, embeds, pages, mediaproxy, ns_settings)


def register_all(app):
    for mod in (media, downloads, search, subreddit, home, comments, avatars,
                users, live, embeds, ns_settings, pages, mediaproxy):
        app.register_blueprint(mod.bp)
