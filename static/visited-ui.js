import { state } from './state.js';
import { settings } from './settings.js';
import { markVisited, isVisited } from './visited.js';

const feed = document.getElementById('feed');

// In-memory only — not persisted. Posts enter when they scroll past the top
// in the current session, driving post-read-hidden without hiding everything
// on a fresh page load.
const _hideSet = new Set();

function _shouldHideReadPosts() {
  if (state.profileMode || state.searchMode || state.duplicatesMode || state.multiMode || state.liveMode) return false;
  const homeSub = (settings.homeSub || 'popular').toLowerCase();
  const cur = (state.currentSub || '').toLowerCase();
  const isHome = cur === homeSub || cur === 'popular' || cur === 'all';
  return isHome ? settings.hideReadHome : settings.hideReadSub;
}

function _applyVisitedEl(el) {
  el.classList.add('post-visited');
}

function _markScrolledPast(el, id) {
  el.classList.add('post-visited');
  _hideSet.add(id);
}

export function _markPostVisited(id) {
  if (!id) return;
  markVisited(id);
  const el = feed.querySelector(`[data-post-id="${CSS.escape(id)}"]`);
  if (el) _applyVisitedEl(el);
}

export function applyVisitedHiding() {
  if (_shouldHideReadPosts()) {
    feed.querySelectorAll('.post-visited').forEach(el => {
      if (_hideSet.has(el.dataset.postId)) el.classList.add('post-read-hidden');
    });
  } else {
    feed.querySelectorAll('.post-read-hidden').forEach(el => el.classList.remove('post-read-hidden'));
  }
}

export function clearVisitedHiding() {
  _hideSet.clear();
  feed.querySelectorAll('.post-visited, .post-read-hidden').forEach(el => el.classList.remove('post-visited', 'post-read-hidden'));
}

const _scrollReadObserver = new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting && entry.boundingClientRect.bottom < 0) {
      const el = entry.target;
      const id = el.dataset.postId;
      if (id && settings.markRead) { markVisited(id); _markScrolledPast(el, id); }
    }
  }
}, { threshold: 0 });

new MutationObserver(mutations => {
  for (const m of mutations) {
    for (const node of m.addedNodes) {
      if (node.nodeType !== 1 || !node.dataset?.postId) continue;
      _scrollReadObserver.observe(node);
      const id = node.dataset.postId;
      if (isVisited(id)) node.classList.add('post-visited');
      if (_hideSet.has(id) && _shouldHideReadPosts()) node.classList.add('post-read-hidden');
    }
  }
}).observe(feed, { childList: true });
