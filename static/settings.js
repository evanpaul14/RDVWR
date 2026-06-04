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
  theme: 'dark',
  pagination: false,
};

function _load() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; }
  catch { return { ...DEFAULTS }; }
}

export const settings = _load();

export function saveSettings() {
  localStorage.setItem(KEY, JSON.stringify(settings));
  applySettings();
}

export function applySettings() {
  document.body.classList.toggle('nsfw-blur', settings.nsfwBlur);
  document.body.classList.toggle('nsfw-hide', settings.nsfwHide);
  document.body.classList.toggle('pagination-mode', !!settings.pagination);
  document.body.classList.remove('theme-light', 'theme-dark', 'theme-system');
  document.body.classList.add(`theme-${settings.theme || 'dark'}`);
}
