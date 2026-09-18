// Locally saved posts (no Reddit account involved). Full post objects are kept
// in localStorage, newest first, so the Saved view renders without any fetch.
import { storeGet, storeSet } from './storage.js';
import { escHtml } from './utils.js';

const KEY       = 'rdvwr_saved';
const MAX_SAVED = 500;
const KNOWN_MAX = 1000;

// Recently rendered posts by id, so a save button only needs the post id.
const _known = new Map();

function _load() {
  try {
    const arr = JSON.parse(storeGet(KEY) || '[]');
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}

let _saved = _load();
let _savedIds = new Set(_saved.map(p => p.id));

export function rememberPost(p) {
  if (!p?.id) return;
  _known.delete(p.id);
  _known.set(p.id, p);
  if (_known.size > KNOWN_MAX) _known.delete(_known.keys().next().value);
}

export function isSaved(id) { return Boolean(id && _savedIds.has(id)); }

export function getSavedPosts() { return _saved.slice(); }

/** Toggle a post's saved state. Returns the new state, or null if it couldn't be stored. */
export function toggleSaved(id) {
  const next = isSaved(id)
    ? _saved.filter(p => p.id !== id)
    : (() => {
        const p = _known.get(id);
        if (!p) return null;
        return [{ ...p, _saved_at: Date.now() }, ..._saved].slice(0, MAX_SAVED);
      })();
  if (!next) return null;
  if (!storeSet(KEY, JSON.stringify(next))) return null;
  _saved = next;
  _savedIds = new Set(next.map(p => p.id));
  return _savedIds.has(id);
}

function _saveSvg(saved) {
  return `<svg width="12" height="12" viewBox="0 0 16 16" fill="${saved ? 'currentColor' : 'none'}" aria-hidden="true"><path d="M4 2h8a1 1 0 0 1 1 1v11l-5-3.2L3 14V3a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>`;
}

export function saveBtnLabel(saved, minimal) {
  return minimal ? (saved ? 'unsave' : 'save') : _saveSvg(saved);
}

export function saveBtnHtml(p, { minimal = false } = {}) {
  const saved = isSaved(p.id);
  const label = saved ? 'Remove from saved' : 'Save post';
  return `<button class="share-btn save-btn${minimal ? ' min-share' : ''}${saved ? ' is-saved' : ''}" data-save="${escHtml(p.id)}"${minimal ? ' data-minimal="1"' : ''} title="${label}" aria-label="${label}" aria-pressed="${saved}">${saveBtnLabel(saved, minimal)}</button>`;
}
