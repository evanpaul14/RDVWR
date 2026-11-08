// Keep a bounded home-feed history for the current source/account. Switching
// identity resets history rather than reusing another account's cursors or seen IDs.
import { storeGet, storeSet } from './storage.js';

const KEY = 'rdvwr_home_state_v2';
const SEEN_MAX = 1000;
const CURSOR_TTL = 30 * 60 * 1000;
let _scope, _seen, _cursors;
let _cookieIdentity, _cookieScope;

export async function homeFeedScope(mode, identity = '') {
  if (mode !== 'cookies') return `${mode === 'login' ? 'login' : 'frontpage'}:${identity.toLowerCase() || 'anonymous'}`;
  // Never persist the cookie header as a storage key or account identifier.
  if (_cookieIdentity !== identity) {
    _cookieIdentity = identity;
    _cookieScope = (async () => {
      try {
        const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(identity));
        return 'cookies:' + Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
      } catch {
        // WebCrypto is unavailable on insecure HTTP. Use page-local history there,
        // so a reload resets cookie-backed history without storing the credential.
        return `cookies:temporary:${Date.now()}:${Math.random()}`;
      }
    })();
  }
  return _cookieScope;
}

function _useScope(scope) {
  if (_scope === scope) return;
  _scope = scope;
  _seen = new Set();
  _cursors = {};
  try {
    const saved = JSON.parse(storeGet(KEY) || 'null');
    if (saved?.scope !== scope) return;
    _seen = new Set(Array.isArray(saved.seen) ? saved.seen.slice(-SEEN_MAX) : []);
    _cursors = saved.cursors && typeof saved.cursors === 'object' ? saved.cursors : {};
  } catch {}
}

function _save() {
  storeSet(KEY, JSON.stringify({ scope: _scope, seen: [..._seen], cursors: _cursors }));
}

export function isHomeSeen(id, scope) {
  _useScope(scope);
  return Boolean(id && _seen.has(id));
}

export function markHomeSeen(ids, scope) {
  _useScope(scope);
  for (const id of ids) {
    if (!id || _seen.has(id)) continue;
    _seen.add(id);
    if (_seen.size > SEEN_MAX) _seen.delete(_seen.values().next().value);
  }
  _save();
}

function _cursorKey(sort, time) { return `${sort}:${time || ''}`; }

export function getHomeCursor(sort, time, scope) {
  _useScope(scope);
  const cursor = _cursors[_cursorKey(sort, time)];
  return cursor && Date.now() - cursor.ts <= CURSOR_TTL ? cursor : null;
}

export function setHomeCursor(sort, time, after, distance, scope) {
  _useScope(scope);
  if (after) _cursors[_cursorKey(sort, time)] = { after, distance, ts: Date.now() };
  else delete _cursors[_cursorKey(sort, time)];
  _save();
}
