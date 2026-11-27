"""Every deployment setting read from the environment, in one place.

Read once at import time. Modules import the values they need from here (e.g.
`from core.config import DISABLE_DOWNLOADS`), so tests can still patch the name on the
module that uses it. This module imports nothing from the project, so anything can import it.

Not here: the standard proxy variables (ALL_PROXY / HTTP(S)_PROXY, read by reddit/client.py
the way curl and requests read them) and GITHUB_DISPATCH_TOKEN (read per call in
gql/client.py)."""
import logging
import os

log = logging.getLogger(__name__)

_TRUTHY = ('1', 'true', 'yes', 'on')
_FALSY = ('0', 'false', 'no', 'off')


def bool_env(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def enum_env(name, default, allowed):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    if raw in allowed:
        return raw
    log.warning("Ignoring invalid %s=%r (expected one of %s)", name, raw, ', '.join(sorted(allowed)))
    return default


def int_env(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        log.warning("Ignoring invalid %s=%r (expected a whole number)", name, os.environ.get(name))
        return default


# ── Allowed values for sorts, time windows and display settings ──────────────────
FEED_SORTS    = {'best', 'hot', 'new', 'top', 'rising', 'controversial'}
SUB_SORTS     = FEED_SORTS - {'best'}   # sorts offered as a per-visitor default
TIME_FILTERS  = {"hour", "day", "week", "month", "year", "all"}
COMMENT_SORTS = {'confidence', 'top', 'new', 'controversial', 'old', 'qa'}
SEARCH_SORTS  = {'relevance', 'hot', 'top', 'new'}
THEMES        = {'dark', 'light', 'system'}
LAYOUTS       = {'card', 'compact', 'minimal'}


# ── Serving ──────────────────────────────────────────────────────────────────────
# Serve under a URL prefix (e.g. /rdvwr) behind a proxy that strips it; see core/base_path.py.
BASE_PATH = os.environ.get('BASE_PATH', '').strip().strip('/')
# Route Reddit/Imgur media through /api/m/ instead of letting the browser hit the CDNs.
PROXY_MEDIA = os.environ.get('PROXY_MEDIA', '0') == '1'
# Share cache and rate-limit state across processes through Redis; unset keeps it in-process.
REDIS_URL = os.environ.get('REDIS_URL', '').strip()
# Requests per client IP per minute (0 turns the bucket off); see core/ratelimit.py.
RATE_LIMIT = int_env('RATE_LIMIT', 600)
RATE_LIMIT_DOWNLOADS = int_env('RATE_LIMIT_DOWNLOADS', 20)
# ffmpeg merges /api/download/reddit-video runs at once per process; more get a 503.
FFMPEG_MAX_JOBS = max(1, int_env('FFMPEG_MAX_JOBS', 2))


# ── Features ─────────────────────────────────────────────────────────────────────
DISABLE_DOWNLOADS = bool_env('DISABLE_DOWNLOADS', False)
DISABLE_NSFW = bool_env('DISABLE_NSFW', False)
# Personalized (cookie-backed) home feed is on by default; set to disable it entirely —
# the frontend hides the Reddit-cookies settings UI and /api/home ignores any cookie sent.
DISABLE_PERSONALIZED_HOME = bool_env('RDVWR_DISABLE_PERSONALIZED_HOME', False)
# Deployer opt-in for the search "only NSFW" checkbox: off by default, and while it's off the
# settings panel doesn't offer the toggle either. When on, the toggle appears and defaults on.
NSFW_ONLY_SEARCH = bool_env('RDVWR_ENABLE_NSFW_ONLY_SEARCH', False)
IMGUR_CLIENT_ID = os.environ.get('IMGUR_CLIENT_ID', '')


# ── Reddit access ────────────────────────────────────────────────────────────────
# Android device-token OAuth; on unless explicitly turned off.
REDDIT_OAUTH = os.environ.get('REDDIT_OAUTH', '1').strip().lower() not in _FALSY
# Optional local account login (reddit/login.py) and where its cookies are stored.
REDDIT_LOGIN = bool_env('REDDIT_LOGIN', False)
REDDIT_LOGIN_FILE = os.environ.get('REDDIT_LOGIN_FILE') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.reddit_login.json')
# Passphrase that lets the owner use the logged-in account from any device (reddit/owner.py).
REDDIT_LOGIN_OWNER_KEY = os.environ.get('REDDIT_LOGIN_OWNER_KEY', '').strip()


# ── Default visitor settings ─────────────────────────────────────────────────────
# Deployer-configurable defaults for the client-side settings a visitor would otherwise
# only get from settings.js's own DEFAULTS. Each maps to the matching key in that file's
# DEFAULTS object; anything a visitor changes in the settings panel is saved to their own
# localStorage and always wins over these (see settings.js _load()). `redditCookies` isn't
# here since it's a per-visitor credential, not a deployment-wide default.
DEFAULT_SETTINGS = {
    'theme':             enum_env('RDVWR_DEFAULT_THEME', 'dark', THEMES),
    'layout':            enum_env('RDVWR_DEFAULT_LAYOUT', 'card', LAYOUTS),
    'subSort':           enum_env('RDVWR_DEFAULT_SUB_SORT', 'hot', SUB_SORTS),
    'subTime':           enum_env('RDVWR_DEFAULT_SUB_TIME', 'day', TIME_FILTERS),
    'commentSort':       enum_env('RDVWR_DEFAULT_COMMENT_SORT', 'confidence', COMMENT_SORTS),
    'homeFeed':          enum_env('RDVWR_DEFAULT_HOME_FEED', 'personalized', {'personalized', 'subscribed', 'popular'}),
    'personalizedHome':  bool_env('RDVWR_DEFAULT_PERSONALIZED_HOME', True),
    'pagination':        bool_env('RDVWR_DEFAULT_PAGINATION', False),
    'showAvatars':       bool_env('RDVWR_DEFAULT_SHOW_AVATARS', False),
    'showAwards':        bool_env('RDVWR_DEFAULT_SHOW_AWARDS', True),
    'linkExternalMedia': bool_env('RDVWR_DEFAULT_LINK_EXTERNAL_MEDIA', False),
    'translateButton':   enum_env('RDVWR_DEFAULT_TRANSLATE_BUTTON', 'auto', {'auto', 'always', 'never'}),
    'nsfwBlur':          bool_env('RDVWR_DEFAULT_NSFW_BLUR', False),
    'nsfwHide':          bool_env('RDVWR_DEFAULT_NSFW_HIDE', False),
    'nsfwSearchHide':    bool_env('RDVWR_DEFAULT_NSFW_SEARCH_HIDE', False),
    'nsfwOnlySearch':    NSFW_ONLY_SEARCH,
    'markRead':          bool_env('RDVWR_DEFAULT_MARK_READ', True),
    'hideReadHome':      bool_env('RDVWR_DEFAULT_HIDE_READ_HOME', False),
    'hideReadSub':       bool_env('RDVWR_DEFAULT_HIDE_READ_SUB', False),
}
