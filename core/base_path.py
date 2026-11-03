"""Serving the app under a URL prefix (BASE_PATH=/rdvwr) behind a proxy that strips it
(Caddy's handle_path). Routes stay root-relative; this only prefixes the URLs we emit.
Unset (the default), every helper here is a no-op."""
import os
import re

_raw = os.environ.get('BASE_PATH', '').strip().strip('/')
BASE_PATH = f'/{_raw}' if _raw else ''

# Root-absolute URL attributes in HTML. Skips '//host' and anything already prefixed, so
# markup that was prefixed at generation time (JSON payloads, sanitized comment HTML)
# passes through unchanged.
_ATTR_RE = re.compile(r'(\b(?:href|src|action|formaction|poster)=)(["\'])/(?!/)(?!' + re.escape(_raw) + r'(?:[/?#"\']))')


def app_path(path):
    """A root-relative path ('/api/img?...') as the browser must request it."""
    return BASE_PATH + path if BASE_PATH and path.startswith('/') and not path.startswith('//') else path


class _PrefixMiddleware:
    """Tell Flask it's mounted at BASE_PATH so url_for() emits prefixed URLs. PATH_INFO
    is left alone: the proxy already stripped the prefix."""
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        environ['SCRIPT_NAME'] = BASE_PATH
        return self.wsgi_app(environ, start_response)


def register(app):
    if not BASE_PATH:
        return
    app.wsgi_app = _PrefixMiddleware(app.wsgi_app)

    @app.after_request
    def _prefix_response(resp):
        loc = resp.headers.get('Location')
        if loc:
            resp.headers['Location'] = app_path(loc) if not loc.startswith(BASE_PATH + '/') else loc
        if (resp.mimetype == 'text/html' and not resp.direct_passthrough and not resp.is_streamed):
            resp.set_data(_ATTR_RE.sub(rf'\1\2{BASE_PATH}/', resp.get_data(as_text=True)))
        return resp
