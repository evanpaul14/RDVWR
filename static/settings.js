import { storeGet, storeSet } from './storage.js';

const KEY = 'rdvwr_settings';

export const DEFAULTS = {
  subSort: 'hot',
  subTime: 'day',
  commentSort: 'confidence',
  nsfwBlur: false,
  nsfwHide: false,
  nsfwSearchHide: false,
  markRead: true,
  hideReadHome: false,
  hideReadSub: false,
  redditCookies: '',
  homeFeed: 'personalized',
  theme: 'dark',
  pagination: false,
  layout: 'card',
  showAvatars: false,
  linkExternalMedia: false,
};

function _load() {
  // Deployer-configurable defaults (RDVWR_DEFAULT_* env vars, see helpers.py) come
  // between the hardcoded DEFAULTS and whatever a visitor has saved locally, so a
  // visitor's own choices always win.
  const serverDefaults = window.__DEFAULT_SETTINGS__ || {};
  try {
    const saved = JSON.parse(storeGet(KEY) || '{}');
    if (!saved.layout) {
      if (saved.minimal) saved.layout = 'minimal';
      else if (saved.compact) saved.layout = 'compact';
    }
    return { ...DEFAULTS, ...serverDefaults, ...saved };
  }
  catch { return { ...DEFAULTS, ...serverDefaults }; }
}

export const settings = _load();

export function saveSettings() {
  storeSet(KEY, JSON.stringify(settings));
  applySettings();
}

export function applySettings() {
  document.body.classList.toggle('nsfw-blur', settings.nsfwBlur);
  document.body.classList.toggle('nsfw-hide', settings.nsfwHide);
  document.body.classList.toggle('pagination-mode', !!settings.pagination);
  document.body.classList.toggle('compact-mode', settings.layout === 'compact');
  document.body.classList.toggle('minimal-mode', settings.layout === 'minimal');
  document.body.classList.remove('theme-light', 'theme-dark', 'theme-system');
  document.body.classList.add(`theme-${settings.theme || 'dark'}`);
}
