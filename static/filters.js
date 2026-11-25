// Visitor content filters: hide posts from chosen subreddits, by chosen users, or whose
// title contains a chosen word or phrase. The lists live in settings (filterSubs /
// filterUsers / filterKeywords). Mirrors core/filters.py, which applies the same rules
// to the no-JS pages:
// - a subreddit filter doesn't apply on that subreddit's own page, and a user filter
//   doesn't apply on that user's profile;
// - keywords match whole words in the title, ignoring case;
// - nothing is filtered on the Saved and Account views.
import { settings } from './settings.js';
import { BASE } from './utils.js';

const MAX_ENTRIES = 100;
const MAX_KEYWORD_LEN = 100;

function _dedupe(items) {
  const seen = new Set();
  return items.filter(it => {
    const k = it.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(0, MAX_ENTRIES);
}

function _names(value, prefixRe, validRe) {
  const text = Array.isArray(value) ? value.join('\n') : String(value || '');
  return _dedupe(text.split(/[\s,]+/).map(s => s.replace(prefixRe, '')).filter(s => validRe.test(s)));
}

export const normalizeSubs  = v => _names(v, /^\/?r\//i, /^[A-Za-z0-9_]{1,50}$/);
export const normalizeUsers = v => _names(v, /^\/?u(?:ser)?\//i, /^[A-Za-z0-9_-]{1,30}$/);
export function normalizeKeywords(value) {
  const items = Array.isArray(value) ? value : String(value || '').split('\n');
  return _dedupe(items.filter(s => typeof s === 'string')
    .map(s => s.split(/\s+/).filter(Boolean).join(' ').slice(0, MAX_KEYWORD_LEN))
    .filter(Boolean));
}

const _list = v => (Array.isArray(v) ? v : []);
const _escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

let _kwSrc = null, _kwRes = [];
function _keywordRes() {
  const kws = _list(settings.filterKeywords);
  const src = kws.join('\n');
  if (src !== _kwSrc) {
    _kwSrc = src;
    _kwRes = kws.map(k => new RegExp(`(?<![\\p{L}\\p{N}_])${_escRe(k)}(?![\\p{L}\\p{N}_])`, 'iu'));
  }
  return _kwRes;
}

function _pagePath() {
  let path = location.pathname;
  if (BASE && path.startsWith(BASE)) path = path.slice(BASE.length) || '/';
  return path;
}

/** Whether the visitor's filters hide post `p` on the current page. */
export function isFiltered(p) {
  const subs = _list(settings.filterSubs), users = _list(settings.filterUsers);
  const kws = _keywordRes();
  if (!subs.length && !users.length && !kws.length) return false;
  const path = _pagePath();
  if (/^\/(account|saved)(\/|$)/.test(path)) return false;
  const pageSub = path.match(/^\/r\/([^/+]+)(?:\/|$)/i)?.[1]?.toLowerCase();
  const pageUser = path.match(/^\/u(?:ser)?\/([^/]+)(?:\/|$)/i)?.[1]?.toLowerCase();
  const sub = (p.subreddit || '').toLowerCase();
  const author = (p.author || '').toLowerCase();
  if (sub !== pageSub && subs.some(s => s.toLowerCase() === sub)) return true;
  if (author !== pageUser && users.some(u => u.toLowerCase() === author)) return true;
  return kws.some(re => re.test(p.title || ''));
}
