import { state } from './state.js';
import { settings, saveSettings, DEFAULTS } from './settings.js';
import { clearVisited } from './visited.js';
import { clearVisitedHiding } from './visited-ui.js';
import { escHtml } from './utils.js';
import { parseRoute } from './router.js';
import { renderRoute } from './nav.js';
import { retryFeedLoad } from './refresh.js';
import { updateFeedsBtn } from './feeds-menu.js';
import { changeCommentSort } from './postview.js';

const sentinel = document.getElementById('scroll-sentinel');
const postView = document.getElementById('post-view');

// ── Settings panel ────────────────────────────────────────────────────────────
const settingsPanel   = document.getElementById('settings-panel');
const settingsOverlay = document.getElementById('settings-overlay');
const settingsBody    = document.getElementById('settings-body');

/** Logged in server-side (see reddit_login.py); only ever set for requests from the server's own machine. */
function _hasHomeCredential() {
  return !!(settings.redditCookies || window.__REDDIT_LOGIN__?.username);
}

function _loginSectionHtml() {
  const st = window.__REDDIT_LOGIN__;
  if (!st) return '';
  const hint = '(sign in on reddit.com, then F12 → Application → Cookies → right-click the <code>reddit.com</code> row → Copy all as header value, and paste below)';
  const loginForm = `<form method="post" action="/auth/reddit/login"><textarea class="settings-input settings-textarea" name="cookies" spellcheck="false" autocomplete="off" placeholder="reddit_session=…; token_v2=…"></textarea><button class="settings-action-btn" type="submit">Log in</button></form>`;
  const row = st.username && st.expired
    ? `<div class="settings-row settings-row--stack"><span class="settings-label">Login expired for u/${escHtml(st.username)} <span class="settings-hint">Reddit stopped accepting the saved cookies, so posting and voting are off. ${hint}</span></span>${loginForm}</div>`
    : st.username
    ? `<div class="settings-row"><span class="settings-label">Logged in as u/${escHtml(st.username)}</span><form method="post" action="/auth/reddit/logout"><button class="settings-action-btn" type="submit">Log out</button></form></div>`
    : st.available
    ? `<div class="settings-row settings-row--stack"><span class="settings-label">Not logged in <span class="settings-hint">${hint}</span></span><form method="post" action="/auth/reddit/login"><textarea class="settings-input settings-textarea" name="cookies" spellcheck="false" autocomplete="off" placeholder="reddit_session=…; token_v2=…"></textarea><button class="settings-action-btn" type="submit">Log in</button></form></div>`
    : '<div class="settings-row"><span class="settings-label">Log in from the machine running the server</span></div>';
  return `<div class="settings-section"><div class="settings-section-title">Reddit account</div>${row}</div>`;
}

function _syncHomeRows() {
  const has = _hasHomeCredential();
  const toggleRow = settingsBody.querySelector('#s-personalized-home-row');
  const feedRow = settingsBody.querySelector('#s-home-feed-row');
  if (toggleRow) toggleRow.style.display = has ? '' : 'none';
  if (feedRow) feedRow.style.display = has && settings.personalizedHome ? '' : 'none';
}

function _settingsHtml() {
  const subSortOpts = [['hot','Hot'],['new','New'],['top','Top'],['rising','Rising'],['controversial','Controversial']];
  const timeOpts    = [['all','All time'],['year','Past year'],['month','Past month'],['week','Past week'],['day','Past day'],['hour','Past hour']];
  const csortOpts   = [['confidence','Best'],['top','Top'],['new','New'],['controversial','Controversial'],['old','Old'],['qa','Q&A']];
  const sel = (id, opts, val) =>
    `<select class="settings-select" id="${id}">${opts.map(([v,l])=>`<option value="${v}"${v===val?' selected':''}>${l}</option>`).join('')}</select>`;
  const chk = (id, checked) =>
    `<input type="checkbox" class="settings-toggle" id="${id}"${checked?' checked':''}>`;
  const themeOpts = [['dark','Dark'],['light','Light'],['system','System default']];
  return `
  <div class="settings-section">
    <div class="settings-section-title">Appearance</div>
    <label class="settings-row"><span class="settings-label">Theme</span>${sel('s-theme', themeOpts, settings.theme || 'dark')}</label>
    <label class="settings-row"><span class="settings-label">Layout</span>${sel('s-layout', [['card','Card'],['compact','Compact'],['minimal','Minimal']], settings.layout || 'card')}</label>
  </div>
  <div class="settings-section">
    <div class="settings-section-title">Feed</div>
    <label class="settings-row"><span class="settings-label">Default sort</span>${sel('s-sub-sort', subSortOpts, settings.subSort)}</label>
    <label class="settings-row"><span class="settings-label">Default time</span>${sel('s-sub-time', timeOpts, settings.subTime)}</label>
    <label class="settings-row"><span class="settings-label">Disable infinite scroll</span>${chk('s-pagination', settings.pagination)}</label>
    ${window.__DISABLE_PERSONALIZED_HOME__ ? '' : `
    <label class="settings-row" id="s-personalized-home-row"${_hasHomeCredential() ? '' : ' style="display:none"'}><span class="settings-label">Personalized home feed</span>${chk('s-personalized-home', settings.personalizedHome)}</label>
    <label class="settings-row" id="s-home-feed-row"${_hasHomeCredential() && settings.personalizedHome ? '' : ' style="display:none"'}><span class="settings-label">Home feed</span>${sel('s-home-feed', [['personalized','Personalized'],['subscribed','Subscribed'],['popular','Popular']], settings.homeFeed || 'personalized')}</label>
    <label class="settings-row settings-row--stack"><span class="settings-label">Reddit cookies <span class="settings-hint">(for personalised home feed — open reddit.com, F12 → Application → Cookies → right-click the <code>reddit.com</code> row → Copy all as header value, then paste below)</span></span><textarea class="settings-input settings-textarea" id="s-reddit-cookies" spellcheck="false" autocomplete="off" placeholder="loid=…; token_v2=…; session_tracker=…">${escHtml(settings.redditCookies || '')}</textarea></label>`}
  </div>
  <div class="settings-section">
    <div class="settings-section-title">Comments</div>
    <label class="settings-row"><span class="settings-label">Default sort</span>${sel('s-comment-sort', csortOpts, settings.commentSort)}</label>
    <label class="settings-row"><span class="settings-label">Show profile pictures</span>${chk('s-show-avatars', settings.showAvatars)}</label>
    <label class="settings-row"><span class="settings-label">Show awards</span>${chk('s-show-awards', settings.showAwards)}</label>
  </div>
  <div class="settings-section">
    <div class="settings-section-title">Privacy</div>
    <label class="settings-row"><span class="settings-label">Don't embed third-party media</span>${chk('s-link-external-media', settings.linkExternalMedia)}</label>
  </div>
  <div class="settings-section">
    <div class="settings-section-title">NSFW</div>
    <label class="settings-row"><span class="settings-label">Blur NSFW thumbnails</span>${chk('s-nsfw-blur', settings.nsfwBlur)}</label>
    <label class="settings-row"><span class="settings-label">Hide NSFW posts</span>${chk('s-nsfw-hide', settings.nsfwHide)}</label>
    <label class="settings-row"><span class="settings-label">Hide NSFW content in search</span>${chk('s-nsfw-search-hide', settings.nsfwSearchHide)}</label>
    ${window.__NSFW_ONLY_SEARCH__ ? `<label class="settings-row"><span class="settings-label">Show "NSFW only" option in search</span>${chk('s-nsfw-only-search', settings.nsfwOnlySearch)}</label>` : ''}
  </div>
  ${_loginSectionHtml()}
  <div class="settings-section">
    <div class="settings-section-title">Read history</div>
    <label class="settings-row"><span class="settings-label">Mark posts as read on scroll</span>${chk('s-mark-read', settings.markRead)}</label>
    <div class="settings-row settings-row-action"><span class="settings-label">Clear read history</span><button class="settings-action-btn" id="s-clear-visited">Clear</button></div>
  </div>
  <div class="settings-section">
    <button class="settings-reset-btn" id="s-reset">Reset to defaults</button>
  </div>`;
}

function openSettingsPanel() {
  settingsPanel.classList.add('open');
  settingsOverlay.classList.add('open');
  document.body.style.overflow = 'hidden';
  settingsBody.innerHTML = _settingsHtml();
  bindSettingEvents();
}

function bindSettingEvents() {
  settingsBody.querySelector('#s-theme').addEventListener('change', e => { settings.theme = e.target.value; saveSettings(); });
  settingsBody.querySelector('#s-layout').addEventListener('change', e => { settings.layout = e.target.value; saveSettings(); retryFeedLoad(); });
  settingsBody.querySelector('#s-sub-sort').addEventListener('change', e => { settings.subSort = e.target.value; saveSettings(); });
  settingsBody.querySelector('#s-sub-time').addEventListener('change', e => { settings.subTime = e.target.value; saveSettings(); });
  settingsBody.querySelector('#s-reddit-cookies')?.addEventListener('change', e => {
    settings.redditCookies = e.target.value.trim();
    saveSettings();
    updateFeedsBtn();
    _syncHomeRows();
    const route = parseRoute();
    if (route.type === 'home') renderRoute({ ...route, after: null });
  });
  settingsBody.querySelector('#s-personalized-home')?.addEventListener('change', e => {
    settings.personalizedHome = e.target.checked;
    saveSettings();
    updateFeedsBtn();
    _syncHomeRows();
    if (parseRoute().type === 'home') renderRoute(parseRoute());
  });
  settingsBody.querySelector('#s-home-feed')?.addEventListener('change', e => {
    settings.homeFeed = e.target.value;
    saveSettings();
    updateFeedsBtn();
    if (parseRoute().type === 'home') renderRoute(parseRoute());
  });
  settingsBody.querySelector('#s-comment-sort').addEventListener('change', e => {
    settings.commentSort = e.target.value;
    state.currentCommentSort = e.target.value;
    saveSettings();
  });
  settingsBody.querySelector('#s-pagination').addEventListener('change', e => {
    settings.pagination = e.target.checked;
    if (!e.target.checked) sentinel.innerHTML = '';
    saveSettings();
  });
  settingsBody.querySelector('#s-show-avatars').addEventListener('change', e => {
    settings.showAvatars = e.target.checked;
    saveSettings();
    if (postView.classList.contains('open')) changeCommentSort(state.currentCommentSort);
  });
  settingsBody.querySelector('#s-show-awards').addEventListener('change', e => {
    settings.showAwards = e.target.checked;
    saveSettings();
    retryFeedLoad();
  });
  settingsBody.querySelector('#s-link-external-media').addEventListener('change', e => {
    settings.linkExternalMedia = e.target.checked;
    saveSettings();
    retryFeedLoad();
  });
  settingsBody.querySelector('#s-nsfw-blur').addEventListener('change', e => { settings.nsfwBlur = e.target.checked; saveSettings(); });
  settingsBody.querySelector('#s-nsfw-hide').addEventListener('change', e => { settings.nsfwHide = e.target.checked; saveSettings(); });
  settingsBody.querySelector('#s-nsfw-search-hide').addEventListener('change', e => { settings.nsfwSearchHide = e.target.checked; saveSettings(); });
  settingsBody.querySelector('#s-nsfw-only-search')?.addEventListener('change', e => { settings.nsfwOnlySearch = e.target.checked; saveSettings(); });
  settingsBody.querySelector('#s-mark-read').addEventListener('change', e => { settings.markRead = e.target.checked; saveSettings(); });
  settingsBody.querySelector('#s-clear-visited').addEventListener('click', () => {
    clearVisited();
    clearVisitedHiding();
  });
  settingsBody.querySelector('#s-reset').addEventListener('click', () => {
    Object.assign(settings, DEFAULTS);
    state.currentCommentSort = DEFAULTS.commentSort;
    saveSettings();
    updateFeedsBtn();
    settingsBody.innerHTML = _settingsHtml();
    bindSettingEvents();
  });
}

function closeSettingsPanel() {
  settingsPanel.classList.remove('open');
  settingsOverlay.classList.remove('open');
  settingsPanel.style.transform = '';
  settingsPanel.style.transition = '';
  if (!postView.classList.contains('open')) document.body.style.overflow = '';
}

// Settings panel swipe-to-close
let _settingsSwipeX = 0;
settingsPanel.addEventListener('touchstart', e => {
  _settingsSwipeX = e.touches[0].clientX;
}, { passive: true });
settingsPanel.addEventListener('touchmove', e => {
  const dx = e.touches[0].clientX - _settingsSwipeX;
  if (dx > 0) {
    e.preventDefault();
    settingsPanel.classList.add('settings-dragging');
    settingsPanel.style.transform = `translateX(${dx}px)`;
  }
}, { passive: false });
settingsPanel.addEventListener('touchend', e => {
  settingsPanel.classList.remove('settings-dragging');
  const dx = e.changedTouches[0].clientX - _settingsSwipeX;
  _settingsSwipeX = 0;
  settingsPanel.style.transform = '';
  if (dx >= 100) closeSettingsPanel();
}, { passive: true });

document.getElementById('settings-btn').addEventListener('click', openSettingsPanel);
document.getElementById('settings-close').addEventListener('click', closeSettingsPanel);
settingsOverlay.addEventListener('click', closeSettingsPanel);
document.getElementById('bn-settings').addEventListener('click', openSettingsPanel);

export { closeSettingsPanel, settingsPanel };
