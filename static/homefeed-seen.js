// Tracks which home-feed post IDs have already been shown to the user, and
// remembers the pagination cursor per sort/time so a plain refresh continues
// deeper into Reddit's feed instead of re-requesting the same first page.
const SEEN_KEY   = 'rdvwr_home_seen';
const SEEN_MAX   = 1000;
const CURSOR_KEY = 'rdvwr_home_cursor';
const CURSOR_TTL = 30 * 60 * 1000; // stale after 30 min of inactivity

function _loadSeen() {
  try { return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) || '[]')); }
  catch { return new Set(); }
}

let _seen = _loadSeen();

export function isHomeSeen(id) { return Boolean(id && _seen.has(id)); }

export function markHomeSeen(ids) {
  let changed = false;
  for (const id of ids) {
    if (!id || _seen.has(id)) continue;
    _seen.add(id);
    changed = true;
    if (_seen.size > SEEN_MAX) _seen.delete(_seen.values().next().value);
  }
  if (changed) localStorage.setItem(SEEN_KEY, JSON.stringify([..._seen]));
}

function _cursorKey(sort, time) { return `${sort}:${time || ''}`; }

export function getHomeCursor(sort, time) {
  try {
    const all = JSON.parse(localStorage.getItem(CURSOR_KEY) || '{}');
    const c = all[_cursorKey(sort, time)];
    if (!c || Date.now() - c.ts > CURSOR_TTL) return null;
    return c;
  } catch { return null; }
}

export function setHomeCursor(sort, time, after, distance) {
  let all;
  try { all = JSON.parse(localStorage.getItem(CURSOR_KEY) || '{}'); }
  catch { all = {}; }
  if (!after) {
    delete all[_cursorKey(sort, time)];
  } else {
    all[_cursorKey(sort, time)] = { after, distance, ts: Date.now() };
  }
  localStorage.setItem(CURSOR_KEY, JSON.stringify(all));
}
