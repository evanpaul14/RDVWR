"""Flask blueprints, one per feature area. register_all() wires them onto the app."""
from routes import (media, downloads, search, subreddit, home, comments, avatars,
                    users, live, embeds, pages, mediaproxy)


def register_all(app):
    for mod in (media, downloads, search, subreddit, home, comments, avatars,
                users, live, embeds, pages, mediaproxy):
        app.register_blueprint(mod.bp)
