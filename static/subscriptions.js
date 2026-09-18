// Locally subscribed subreddits (no Reddit account involved). Their combined
// feed replaces the anonymous home feed, or is offered as its own feed when a
// personalized (cookie-backed) home feed is configured.
import { storeGet, storeSet } from './storage.js';

const KEY      = 'rdvwr_subs';
// The combined feed is fetched as /r/a+b+c, which the backend caps at 50 names.
export const MAX_SUBS = 50;

function _load() {
  try {
    const arr = JSON.parse(storeGet(KEY) || '[]');
    return Array.isArray(arr) ? arr.filter(s => typeof s === 'string') : [];
  } catch { return []; }
}

let _subs = _load();

export function getSubs() { return _subs.slice(); }

export function isSubscribed(sub) {
  const key = (sub || '').toLowerCase();
  return _subs.some(s => s.toLowerCase() === key);
}

/** A plain subreddit name that can be subscribed to (not popular/all or a combined a+b feed). */
export function isSubscribable(sub) {
  return /^[A-Za-z0-9_]{1,50}$/.test(sub || '') && !['popular', 'all'].includes(sub.toLowerCase());
}

/** Toggle a subscription. Returns the new state, or null if it couldn't be stored or the cap is hit. */
export function toggleSub(sub) {
  const subscribed = isSubscribed(sub);
  if (!subscribed && _subs.length >= MAX_SUBS) return null;
  const next = subscribed
    ? _subs.filter(s => s.toLowerCase() !== sub.toLowerCase())
    : [..._subs, sub].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
  if (!storeSet(KEY, JSON.stringify(next))) return null;
  _subs = next;
  return !subscribed;
}
