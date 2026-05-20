const KEY = 'rdvwr_settings';

export const DEFAULTS = {
  subSort: 'hot',
  subTime: 'all',
  commentSort: 'confidence',
  nsfwBlur: false,
  nsfwHide: false,
  homeSub: 'popular',
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
}
