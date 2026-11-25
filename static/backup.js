// Settings backup: export this browser's settings, filters and subscriptions as a
// copyable code and as a restore link (/settings/restore?data=<code>), and import
// either one. Same format as core/backup.py: base64url JSON
// {v: 1, settings: {...settings.js keys}, subscriptions: [...]}.
// Never included: pasted Reddit cookies, saved posts, read history.
import { settings, DEFAULTS, saveSettings } from './settings.js';
import { getSubs, isSubscribable, MAX_SUBS } from './subscriptions.js';
import { normalizeSubs, normalizeUsers, normalizeKeywords } from './filters.js';
import { storeSet } from './storage.js';
import { BASE, escHtml } from './utils.js';

const VERSION = 1;
const SUBS_KEY = 'rdvwr_subs';
const EXCLUDED = new Set(['redditCookies']);

const ENUMS = {
  theme: ['dark', 'light', 'system'],
  layout: ['card', 'compact', 'minimal'],
  subSort: ['hot', 'new', 'top', 'rising', 'controversial'],
  subTime: ['hour', 'day', 'week', 'month', 'year', 'all'],
  commentSort: ['confidence', 'top', 'new', 'controversial', 'old', 'qa'],
  homeFeed: ['personalized', 'subscribed', 'popular'],
  translateButton: ['auto', 'always', 'never'],
};
const LISTS = { filterSubs: normalizeSubs, filterUsers: normalizeUsers, filterKeywords: normalizeKeywords };

function _b64urlEncode(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function _b64urlDecode(code) {
  const b64 = code.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - code.length % 4) % 4);
  const bin = atob(b64);
  return new TextDecoder().decode(Uint8Array.from(bin, c => c.charCodeAt(0)));
}

export function exportCode() {
  const out = {};
  for (const key of Object.keys(DEFAULTS)) if (!EXCLUDED.has(key)) out[key] = settings[key];
  return _b64urlEncode(JSON.stringify({ v: VERSION, settings: out, subscriptions: getSubs() }));
}

export function restoreLink(code = exportCode()) {
  return `${location.origin}${BASE}/settings/restore?data=${code}`;
}

/** The backup in a code or restore link, or null if it isn't one. */
export function parseBackup(text) {
  text = String(text || '').trim();
  if (text.includes('?') || /^(https?:|\/)/.test(text)) {
    try { text = new URL(text, location.origin).searchParams.get('data') || ''; } catch { return null; }
  }
  if (!text || text.length > 20000) return null;
  try {
    const data = JSON.parse(_b64urlDecode(text));
    if (!data || data.v !== VERSION || typeof (data.settings ?? {}) !== 'object' || Array.isArray(data.settings)) return null;
    if (!Array.isArray(data.subscriptions ?? [])) return null;
    return { settings: data.settings || {}, subscriptions: data.subscriptions || [] };
  } catch { return null; }
}

/** Apply a parsed backup over the current settings. Only keys present and valid in the
 *  backup change; subscriptions are replaced when the backup has any. Returns counts. */
export function applyBackup({ settings: incoming, subscriptions }) {
  let applied = 0;
  for (const [key, def] of Object.entries(DEFAULTS)) {
    if (EXCLUDED.has(key) || !(key in incoming)) continue;
    const v = incoming[key];
    if (LISTS[key]) { if (Array.isArray(v)) { settings[key] = LISTS[key](v); applied++; } }
    else if (ENUMS[key]) { if (ENUMS[key].includes(v)) { settings[key] = v; applied++; } }
    else if (typeof v === typeof def) { settings[key] = v; applied++; }
  }
  saveSettings();
  const seen = new Set();
  const subs = subscriptions.filter(s => typeof s === 'string' && isSubscribable(s) && !seen.has(s.toLowerCase()) && seen.add(s.toLowerCase()))
    .slice(0, MAX_SUBS).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
  if (subs.length) storeSet(SUBS_KEY, JSON.stringify(subs));
  return { settings: applied, subscriptions: subs.length };
}

export function backupSectionHtml(importCode = '') {
  const code = exportCode();
  const copyBtn = target => `<button type="button" class="settings-action-btn" data-copy="${target}">Copy</button>`;
  return `
  <div class="settings-section" id="s-backup">
    <div class="settings-section-title">Backup &amp; restore</div>
    <div class="settings-row settings-row--stack"><span class="settings-label">Restore link <span class="settings-hint">(open it in another browser to restore settings, filters and subscriptions there; doesn't include Reddit cookies, saved posts or read history)</span></span>
      <div class="settings-copy-row"><input class="settings-input" id="s-backup-link" type="text" readonly value="${escHtml(restoreLink(code))}">${copyBtn('s-backup-link')}</div></div>
    <div class="settings-row settings-row--stack"><span class="settings-label">Settings code <span class="settings-hint">(the same, as text to paste into Import)</span></span>
      <div class="settings-copy-row"><textarea class="settings-input settings-textarea" id="s-backup-code" readonly>${escHtml(code)}</textarea>${copyBtn('s-backup-code')}</div></div>
    <div class="settings-row settings-row--stack"><span class="settings-label">Import <span class="settings-hint">(paste a settings code or restore link)</span></span>
      <textarea class="settings-input settings-textarea" id="s-backup-import" spellcheck="false" autocomplete="off">${escHtml(importCode)}</textarea>
      <div class="settings-copy-row"><button type="button" class="settings-action-btn" id="s-backup-import-btn">Import</button><span class="settings-hint" id="s-backup-msg" role="status">${importCode ? 'Opened from a restore link. Import to replace this browser\'s settings.' : ''}</span></div></div>
  </div>`;
}

/** Refresh the export fields after a setting changes while the panel is open. */
export function refreshBackupFields(root) {
  const code = exportCode();
  const codeEl = root.querySelector('#s-backup-code');
  const linkEl = root.querySelector('#s-backup-link');
  if (codeEl) codeEl.value = code;
  if (linkEl) linkEl.value = restoreLink(code);
}

function _copy(el, btn) {
  const done = () => { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); };
  if (navigator.clipboard) navigator.clipboard.writeText(el.value).then(done).catch(() => { el.select(); });
  else { el.select(); try { document.execCommand('copy'); done(); } catch { /* selected for manual copy */ } }
}

export function bindBackup(root) {
  root.querySelectorAll('[data-copy]').forEach(btn => btn.addEventListener('click', () => {
    const el = root.querySelector('#' + btn.dataset.copy);
    if (el) _copy(el, btn);
  }));
  root.querySelector('#s-backup-import-btn').addEventListener('click', () => {
    const msg = root.querySelector('#s-backup-msg');
    const data = parseBackup(root.querySelector('#s-backup-import').value);
    if (!data) { msg.textContent = 'That code is invalid or incomplete. Check that all of it was copied.'; return; }
    applyBackup(data);
    msg.textContent = 'Restored. Reloading…';
    location.assign(`${BASE}/`);
  });
}
