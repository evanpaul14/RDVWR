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
  layout: 'card',
};

function _load() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || '{}');
    if (!saved.layout) {
      if (saved.minimal) saved.layout = 'minimal';
      else if (saved.compact) saved.layout = 'compact';
    }
    return { ...DEFAULTS, ...saved };
  }
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
  document.body.classList.toggle('compact-mode', settings.layout === 'compact');
  document.body.classList.toggle('minimal-mode', settings.layout === 'minimal');
  document.body.classList.remove('theme-light', 'theme-dark', 'theme-system');
  document.body.classList.add(`theme-${settings.theme || 'dark'}`);
  const popularBtn = document.getElementById('popular-btn');
  if (popularBtn) popularBtn.hidden = !settings.redditCookies;
}
