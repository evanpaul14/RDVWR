"""Flask blueprints, one per feature area. register_all() wires them onto the app."""
from routes import (media, downloads, search, subreddit, home, comments, avatars,
                    users, live, embeds, pages)


def register_all(app):
    for mod in (media, downloads, search, subreddit, home, comments, avatars,
                users, live, embeds, pages):
        app.register_blueprint(mod.bp)
