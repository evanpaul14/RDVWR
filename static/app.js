import { state } from './state.js';
import { escHtml, setActiveButton, AUTOCOMPLETE_DEBOUNCE, TOUCH_MOVE_THRESHOLD } from './utils.js';
import { parseRoute } from './router.js';
import {
  loadSubreddit, loadSubFeed,
  loadMultireddit, loadMultiFeed,
  loadProfile, loadProfileTab,
  loadSearch, loadSearchResults, loadCommunityResults, loadUserResults,
  loadDuplicatesPage, loadWikiPage,
  loadLiveThread, loadMoreLiveUpdates, cancelLivePoll,
  loadPostView, closePostView, openPostView, changeCommentSort, loadMoreComments,
  closeSidebar, toggleSidebar,
  retryFeedLoad, errState,
  buildSubSortHtml, buildProfileSortHtml, SEARCH_SORT_BTN_HTML,
  searchTypeBar,
} from './feed.js';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const feed       = document.getElementById('feed');
const sortBar    = document.getElementById('sort-bar');
const sentinel   = document.getElementById('scroll-sentinel');
const subInput   = document.getElementById('subreddit-input');
const pvSubInput = document.getElementById('pv-subreddit-input');
const pvScroll   = document.getElementById('pv-scroll');
const postView   = document.getElementById('post-view');
const pvContent  = document.getElementById('pv-content');
const lightbox   = document.getElementById('lightbox');
const lightboxImg = document.getElementById('lightbox-img');

// ── Navigation ────────────────────────────────────────────────────────────────
export function navigateOrOpen(path, e) {
  if (e && (e.ctrlKey || e.metaKey || e.button === 1)) { window.open(path, '_blank'); return; }
  navigate(path);
}

export function navigate(path, { replace=false }={}) {
  const pvScrollTop = document.getElementById('pv-scroll')?.scrollTop || 0;
  history.replaceState({ ...(history.state||{}), scrollY: window.scrollY, pvScrollTop }, '', location.href);
  if (replace) history.replaceState(null,'',path);
  else         history.pushState(null,'',path);
  renderRoute(parseRoute(path));
}

async function renderRoute(route, { restoreScroll=0, restorePvScroll=0 }={}) {
  if (route.type !== 'search') {
    searchTypeBar.style.display = 'none';
    state.searchType = 'posts';
  }
  if (route.type !== 'duplicates') state.duplicatesMode = false;
  if (route.type !== 'wiki') state.wikiMode = false;
  if (route.type !== 'live') { state.liveMode = false; cancelLivePoll(); }
  switch (route.type) {
    case 'home':
      navigate('/r/popular/hot', { replace: true });
      return;
    case 'sub':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      await loadSubreddit(route.sub, route.sort, route.time || 'all');
      break;
    case 'multi':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      state.profileMode = false;
      await loadMultireddit(route.username, route.multiname, route.sort, route.time || 'all');
      break;
    case 'post':
      if (!feed.querySelector('.post')) await loadSubreddit(route.sub, state.currentSort);
      await loadPostView(route.sub, route.postId, route.commentId||'', restorePvScroll);
      break;
    case 'user':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      await loadProfile(route.username);
      break;
    case 'search':
      closePostView();
      closeSidebar();
      await loadSearch(route.query, route.sort, route.time, route.sub, route.nsfw, route.stype || 'posts');
      break;
    case 'duplicates':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      state.profileMode = false;
      await loadDuplicatesPage(route.sub, route.postId);
      break;
    case 'wiki':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      state.profileMode = false;
      await loadWikiPage(route.sub, route.page);
      break;
    case 'live':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      state.profileMode = false;
      await loadLiveThread(route.threadId);
      break;
  }
  if (route.type !== 'post') window.scrollTo({top: restoreScroll, behavior: 'instant'});
}

window.addEventListener('popstate', (e) => {
  const savedScroll = e.state?.scrollY || 0;
  const savedPvScroll = e.state?.pvScrollTop || 0;
  const route = parseRoute();
  if (route.type !== 'post') closePostView();
  const hasFeedPosts = !!feed.querySelector('.post');
  if (route.type === 'sub' && hasFeedPosts && route.sub === state.currentSub && !state.searchMode && !state.profileMode && !state.duplicatesMode && !state.multiMode) {
    state.currentSort = route.sort;
    state.currentTime = route.time || 'all';
    window.scrollTo({top: savedScroll, behavior: 'instant'});
    return;
  }
  if (route.type === 'multi' && hasFeedPosts && route.username === state.multiUsername && route.multiname === state.multiName && state.multiMode) {
    state.currentSort = route.sort;
    state.currentTime = route.time || 'all';
    window.scrollTo({top: savedScroll, behavior: 'instant'});
    return;
  }
  renderRoute(route, { restoreScroll: savedScroll, restorePvScroll: savedPvScroll });
});

// ── Link interception ─────────────────────────────────────────────────────────
function interceptNavLink(a, e) {
  if (a.getAttribute('data-back')) { e.preventDefault(); history.back(); return true; }
  const datanav = a.getAttribute('data-nav');
  if (datanav) { e.preventDefault(); navigateOrOpen(datanav, e); return true; }

  const href = a.getAttribute('href') || '';
  if (!href || href.startsWith('#') || href.startsWith('javascript:') ||
      href.startsWith('mailto:') || href.startsWith('tel:')) return false;

  const redditLive = href.match(/(?:https?:\/\/(?:www\.)?reddit\.com)\/live\/([A-Za-z0-9_-]+)/);
  if (redditLive) { e.preventDefault(); navigateOrOpen(`/live/${redditLive[1]}`, e); return true; }
  const redditPost = href.match(/(?:https?:\/\/(?:www\.)?reddit\.com)\/r\/([^\/]+)\/comments\/([^\/?\s#]+)/);
  if (redditPost) { e.preventDefault(); navigateOrOpen(`/r/${redditPost[1]}/comments/${redditPost[2]}`, e); return true; }
  const redditWiki = href.match(/(?:https?:\/\/(?:www\.)?reddit\.com)\/r\/([^\/]+)\/wiki(?:\/([^\s#?]*))?/);
  if (redditWiki) { e.preventDefault(); navigateOrOpen(`/r/${redditWiki[1]}/wiki/${redditWiki[2]||'index'}`, e); return true; }
  const redditSub  = href.match(/(?:https?:\/\/(?:www\.)?reddit\.com)\/r\/([^\/?\s#]+)/);
  if (redditSub)  { e.preventDefault(); navigateOrOpen(`/r/${redditSub[1]}`, e); return true; }
  const redditUser = href.match(/(?:https?:\/\/(?:www\.)?reddit\.com)\/u(?:ser)?\/([^\/?\s#]+)/);
  if (redditUser) { e.preventDefault(); navigateOrOpen(`/user/${redditUser[1]}`, e); return true; }
  try {
    const url = new URL(href, location.origin);
    if (url.origin !== location.origin) return false;
    e.preventDefault();
    navigateOrOpen(url.pathname + url.search, e);
    return true;
  } catch { return false; }
}

// ── Event handlers ────────────────────────────────────────────────────────────

// pv-home button
document.getElementById('pv-home').addEventListener('click', () => { navigate('/r/popular/hot'); });

// Comment collapse
document.getElementById('post-view').addEventListener('click', e => {
  const header = e.target.closest('.comment-header');
  if (!header || e.target.tagName==='A') return;
  const authorEl = e.target.closest('.comment-author[data-user]');
  if (authorEl) { navigateOrOpen(`/user/${authorEl.dataset.user}`, e); return; }
  const comment   = header.closest('.comment');
  const collapsed = comment.classList.toggle('collapsed');
  const btn = comment.querySelector(':scope > .comment-header > .comment-collapse');
  if (btn) btn.textContent = collapsed ? '+' : '−';
});

// pvContent: comment sort, load more, retry, user nav
pvContent.addEventListener('click', e => {
  const retryBtn = e.target.closest('.state-retry-btn[data-retry]');
  if (retryBtn) {
    const t = retryBtn.dataset.retry;
    if (t === 'post') loadPostView(state._pvSub, state._pvPostId, state._pvCommentId);
    else if (t === 'comments') changeCommentSort(state.currentCommentSort);
    return;
  }
  const csort = e.target.closest('[data-csort]');
  if (csort) { e.preventDefault(); changeCommentSort(csort.dataset.csort); return; }
  const moreBtn = e.target.closest('.load-more-btn');
  if (moreBtn) { e.preventDefault(); loadMoreComments(moreBtn); return; }
  const btn = e.target.closest('[data-user]');
  if (btn && !e.target.closest('a')) { e.preventDefault(); navigateOrOpen(`/user/${btn.dataset.user}`, e); }
});

// Feed clicks: comments, author, retry
feed.addEventListener('click', e => {
  if (e.defaultPrevented) return;
  const retryBtn = e.target.closest('.state-retry-btn[data-retry]');
  if (retryBtn) { retryFeedLoad(); return; }
  const commBtn = e.target.closest('.comments-link[data-id]');
  const userBtn = e.target.closest('.post-author[data-user]');
  const liveAuthor = e.target.closest('.live-update-author[data-user]');
  if (commBtn) {
    navigateOrOpen(`/r/${commBtn.dataset.sub}/comments/${commBtn.dataset.id}`, e);
  } else if (userBtn) {
    navigateOrOpen(`/user/${userBtn.dataset.user}`, e);
  } else if (liveAuthor) {
    navigateOrOpen(`/user/${liveAuthor.dataset.user}`, e);
  }
});

// Search type tab bar
searchTypeBar.addEventListener('click', e => {
  const btn = e.target.closest('[data-stype]');
  if (!btn || !state.searchMode) return;
  const t = btn.dataset.stype;
  if (t === state.searchType) return;
  state.searchType = t;
  setActiveButton(searchTypeBar, 'stype', t);
  sortBar.style.display = t === 'posts' ? 'flex' : 'none';
  if (t === 'communities') loadCommunityResults(state.searchQuery);
  else if (t === 'users')  loadUserResults(state.searchQuery);
  else                     loadSearchResults(state.searchQuery, state.searchSort, state.searchTime);
});

function buildSearchUrl(q=state.searchQuery, sort=state.searchSort, time=state.searchTime, sub=state.searchSub, nsfw=state.searchNsfw) {
  let url = `/search?q=${encodeURIComponent(q)}&sort=${sort}`;
  if (time !== 'all') url += `&t=${time}`;
  if (sub)  url += `&sub=${encodeURIComponent(sub)}`;
  if (!nsfw) url += `&nsfw=0`;
  return url;
}

// Sort bar click
sortBar.addEventListener('click', e => {
  if (e.target.closest('#sidebar-toggle-btn')) {
    toggleSidebar(state.currentSub);
    return;
  }
  if (e.target.closest('#nsfw-toggle') && state.searchMode) {
    state.searchNsfw = !state.searchNsfw;
    navigate(buildSearchUrl(), { replace:true });
    return;
  }
  const ssortBtn = e.target.closest('.sort-btn[data-ssort]');
  if (ssortBtn && state.searchMode) {
    const newSort = ssortBtn.dataset.ssort;
    if (newSort === state.searchSort) return;
    state.searchSort = newSort; state.searchTime = 'all';
    navigate(buildSearchUrl(), { replace:true });
    return;
  }
  const ptabBtn = e.target.closest('.sort-btn[data-ptab]');
  if (ptabBtn && state.profileMode) {
    if (ptabBtn.dataset.ptab === state.profileTab) return;
    state.profileTab = ptabBtn.dataset.ptab;
    state.profileSort = 'new'; state.profileTime = 'all';
    sortBar.innerHTML = buildProfileSortHtml(state.profileTab, state.profileSort, state.profileTime);
    loadProfileTab(state.profileUser, state.profileTab, state.profileSort, state.profileTime);
    return;
  }
  const psortBtn = e.target.closest('.sort-btn[data-psort]');
  if (psortBtn && state.profileMode) {
    const newSort = psortBtn.dataset.psort;
    if (newSort === state.profileSort) return;
    state.profileSort = newSort; state.profileTime = 'all';
    sortBar.innerHTML = buildProfileSortHtml(state.profileTab, state.profileSort, state.profileTime);
    loadProfileTab(state.profileUser, state.profileTab, state.profileSort, state.profileTime);
    return;
  }
  const sortBtn = e.target.closest('.sort-btn[data-sort]');
  if (!sortBtn || state.profileMode || state.searchMode) return;
  const newSort = sortBtn.dataset.sort;
  if (newSort === state.currentSort) return;
  state.currentSort = newSort; state.currentTime = newSort === 'controversial' ? 'day' : 'all';
  state.afterToken = null;
  window.scrollTo({top:0, behavior:'instant'});
  if (state.multiMode) {
    navigate(`/user/${state.multiUsername}/m/${state.multiName}/${state.currentSort}`, { replace:true });
  } else {
    navigate(`/r/${state.currentSub}/${state.currentSort}`, { replace:true });
  }
});

// Sort bar change (time filter, scope checkbox)
sortBar.addEventListener('change', e => {
  const scopeCheck = e.target.closest('#scope-check');
  if (scopeCheck && state.searchMode) {
    state.searchSub = scopeCheck.checked ? state.searchSubStored : '';
    navigate(buildSearchUrl(), { replace:true });
    return;
  }
  const sel = e.target.closest('#time-filter');
  if (!sel) return;
  if (state.searchMode) {
    state.searchTime = sel.value;
    navigate(buildSearchUrl(), { replace:true });
  } else if (state.profileMode) {
    state.profileTime = sel.value;
    sortBar.innerHTML = buildProfileSortHtml(state.profileTab, state.profileSort, state.profileTime);
    loadProfileTab(state.profileUser, state.profileTab, state.profileSort, state.profileTime);
  } else if (state.multiMode) {
    state.currentTime = sel.value;
    state.afterToken = null;
    window.scrollTo({top:0, behavior:'instant'});
    navigate(`/user/${state.multiUsername}/m/${state.multiName}/${state.currentSort}?t=${state.currentTime}`, { replace:true });
  } else {
    state.currentTime = sel.value;
    state.afterToken = null;
    window.scrollTo({top:0, behavior:'instant'});
    navigate(`/r/${state.currentSub}/${state.currentSort}?t=${state.currentTime}`, { replace:true });
  }
});

// Search input
function handleSearchInput(e) {
  const activeInput = (e?.currentTarget?.id === 'pv-search-btn' || e?.target === pvSubInput || document.activeElement === pvSubInput) ? pvSubInput : subInput;
  const val = activeInput.value.trim();
  if (!val) return;
  const mMultiInput = val.match(/^u(?:ser)?\/([^\/]+)\/m\/([^\/]+)/i);
  if (mMultiInput) { navigate(`/user/${mMultiInput[1]}/m/${mMultiInput[2]}`); return; }
  if (val.startsWith('r/')) {
    const sub = val.slice(2).replace(/^\//, '');
    if (sub) navigate(`/r/${sub}/top`);
  } else {
    const sub = state.searchMode ? state.searchSub : (state.currentSub || '');
    let url = `/search?q=${encodeURIComponent(val)}`;
    if (sub) url += `&sub=${encodeURIComponent(sub)}`;
    navigate(url);
  }
}
function hideAllAutocomplete() {
  document.querySelectorAll('.autocomplete-dropdown').forEach(el => {
    el.classList.remove('open');
    el.innerHTML = '';
  });
}
document.getElementById('search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
document.getElementById('subreddit-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') handleSearchInput();
});
document.getElementById('pv-search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
document.getElementById('pv-subreddit-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') handleSearchInput(e);
});

// Infinite scroll
function loadMore() {
  if (state.loading || state.wikiMode) return;
  if (state.duplicatesMode) {
    if (state.duplicatesAfter) loadDuplicatesPage(state.duplicatesSub, state.duplicatesPostId, state.duplicatesAfter, true);
  } else if (state.searchMode) {
    if (state.searchType === 'communities' && state.communityAfter) loadCommunityResults(state.searchQuery, state.communityAfter, true);
    else if (state.searchType === 'users' && state.userAfter)       loadUserResults(state.searchQuery, state.userAfter, true);
    else if (state.searchAfter)                                      loadSearchResults(state.searchQuery, state.searchSort, state.searchTime, state.searchAfter, true);
  } else if (state.profileMode) {
    if (state.profileAfter) loadProfileTab(state.profileUser, state.profileTab, state.profileSort, state.profileTime, state.profileAfter, true);
  } else if (state.multiMode) {
    if (state.afterToken) loadMultiFeed(state.multiUsername, state.multiName, state.currentSort, state.currentTime, state.afterToken, true);
  } else if (state.liveMode) {
    if (state.liveAfter) loadMoreLiveUpdates(state.liveThreadId, state.liveAfter);
  } else {
    if (state.afterToken) loadSubFeed(state.currentSub, state.currentSort, state.currentTime, state.afterToken, true);
  }
}
new IntersectionObserver(entries => {
  if (entries[0].isIntersecting) loadMore();
}, { rootMargin: '300px' }).observe(sentinel);

// Flair / community / user card clicks
feed.addEventListener('click', e => {
  const flairEl = e.target.closest('.flair.flair-clickable[data-flair]');
  if (flairEl) {
    e.stopPropagation();
    const sub   = flairEl.dataset.sub;
    const flair = flairEl.dataset.flair;
    if (sub && flair) navigateOrOpen(`/search?q=${encodeURIComponent('flair:"'+flair+'"')}&sub=${encodeURIComponent(sub)}&sort=new`, e);
    return;
  }
  const card = e.target.closest('.community-card[data-nav], .user-card[data-nav]');
  if (card) { navigateOrOpen(card.dataset.nav, e); return; }
});
feed.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const card = e.target.closest('.community-card[data-nav], .user-card[data-nav]');
  if (!card) return;
  e.preventDefault();
  navigateOrOpen(card.dataset.nav, e);
});

// Logo
document.getElementById('logo-btn').addEventListener('click', () => navigate('/'));

// Long-press on post card → open in new tab (mobile)
let _longPressTimer = null;
let _longPressTriggered = false;
document.addEventListener('touchstart', e => {
  _longPressTriggered = false;
  const post = e.target.closest('#feed .post, #feed .post-compact');
  if (!post || e.target.closest('a, button, video, iframe, input')) return;
  const titleLink = post.querySelector('a[data-nav]');
  if (!titleLink) return;
  _longPressTimer = setTimeout(() => {
    _longPressTimer = null;
    _longPressTriggered = true;
    if (navigator.vibrate) navigator.vibrate(40);
    window.open(titleLink.dataset.nav, '_blank');
  }, 550);
}, { passive: true });
document.addEventListener('touchmove', e => {
  if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
}, { passive: true });
document.addEventListener('touchend', e => {
  if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
}, { passive: true });

// iOS PWA: intercept in-app links on touchend
let _touchStartX = 0, _touchStartY = 0, _navFromTouch = false;
document.addEventListener('touchstart', e => {
  _touchStartX = e.touches[0].clientX;
  _touchStartY = e.touches[0].clientY;
  _navFromTouch = false;
}, { passive: true });
document.addEventListener('touchend', e => {
  const dx = Math.abs(e.changedTouches[0].clientX - _touchStartX);
  const dy = Math.abs(e.changedTouches[0].clientY - _touchStartY);
  if (dx > TOUCH_MOVE_THRESHOLD || dy > TOUCH_MOVE_THRESHOLD) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank') return;
  if (interceptNavLink(a, e)) _navFromTouch = true;
}, { passive: false });

// Capture-phase click handler
document.addEventListener('click', e => {
  if (_navFromTouch) { _navFromTouch = false; return; }
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank') return;
  interceptNavLink(a, e);
}, true);

// Middle-click
document.addEventListener('auxclick', e => {
  if (e.button !== 1) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank') return;
  interceptNavLink(a, e);
}, true);

// Share / copy link
document.addEventListener('click', e => {
  const btn = e.target.closest('.share-btn[data-share]');
  if (!btn) return;
  e.stopPropagation();
  navigator.clipboard.writeText(location.origin + btn.dataset.share).then(() => {
    const prev = btn.innerHTML;
    btn.innerHTML = '✓ copied';
    btn.classList.add('share-copied');
    setTimeout(() => { btn.innerHTML = prev; btn.classList.remove('share-copied'); }, 1500);
  }).catch(() => {});
});

// Unmute propagation
let _propagatingUnmute = false;
document.addEventListener('volumechange', e => {
  const v = e.target;
  if (v.tagName !== 'VIDEO' || _propagatingUnmute) return;
  if (v.muted && v.volume > 0) {
    if (state.userPrefersMuted) {
      v.muted = false;
    } else {
      state.userPrefersMuted = true;
      localStorage.setItem('mutePreference', 'muted');
    }
  } else if (!v.muted) {
    state.userPrefersMuted = false;
    localStorage.setItem('mutePreference', 'unmuted');
    _propagatingUnmute = true;
    document.querySelectorAll('video').forEach(other => { other.muted = false; });
    _propagatingUnmute = false;
  }
}, true);

// Lightbox
function openLightbox(src) {
  lightboxImg.src = src;
  lightbox.classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeLightbox() {
  lightbox.classList.remove('open');
  document.body.style.overflow = '';
  lightboxImg.src = '';
}
lightbox.addEventListener('click', closeLightbox);
lightboxImg.addEventListener('click', e => e.stopPropagation());
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (lightbox.classList.contains('open')) { closeLightbox(); return; }
    if (postView.classList.contains('open') && state._pvSub) { navigate(`/r/${state._pvSub}`); return; }
  }
});
document.addEventListener('click', e => {
  const img = e.target.closest('.post-media img, .pv-media img, .md img, .gallery-main-img');
  if (!img) return;
  e.stopPropagation();
  openLightbox(img.src);
});

// Subreddit autocomplete
function setupAutocomplete(inputEl, dropdownEl) {
  let acTimer = null;
  let acIdx = -1;
  let preAcVal = '';

  function hide() {
    dropdownEl.classList.remove('open');
    dropdownEl.innerHTML = '';
    acIdx = -1;
    inputEl.setAttribute('aria-expanded', 'false');
  }
  function show(names) {
    if (!names.length) { hide(); return; }
    acIdx = -1;
    dropdownEl.innerHTML = names.map(n =>
      `<div class="autocomplete-item" role="option" data-sub="${escHtml(n)}">${escHtml(n)}</div>`
    ).join('');
    dropdownEl.classList.add('open');
    inputEl.setAttribute('aria-expanded', 'true');
  }

  inputEl.addEventListener('input', () => {
    clearTimeout(acTimer);
    const val = inputEl.value.trim();
    const query = val.startsWith('r/') ? val.slice(2) : val;
    if (query.length < 2) { hide(); return; }
    acTimer = setTimeout(async () => {
      try {
        const res  = await fetch(`/api/subreddit-search?q=${encodeURIComponent(query)}`);
        const data = await res.json();
        show(data.names || []);
      } catch {}
    }, AUTOCOMPLETE_DEBOUNCE);
  });

  inputEl.addEventListener('keydown', e => {
    if (!dropdownEl.classList.contains('open')) return;
    const items = [...dropdownEl.querySelectorAll('.autocomplete-item')];
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (acIdx === -1) preAcVal = inputEl.value;
      acIdx = Math.min(acIdx + 1, items.length - 1);
      items.forEach((item, i) => item.classList.toggle('focused', i === acIdx));
      if (acIdx >= 0) inputEl.value = items[acIdx].dataset.sub;
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      acIdx = Math.max(acIdx - 1, -1);
      items.forEach((item, i) => item.classList.toggle('focused', i === acIdx));
      inputEl.value = acIdx >= 0 ? items[acIdx].dataset.sub : preAcVal;
    } else if (e.key === 'Enter') {
      hide();
      if (acIdx >= 0) {
        e.preventDefault();
        e.stopImmediatePropagation();
        const sub = items[acIdx]?.dataset.sub;
        if (sub) navigate(`/r/${sub}`);
      }
    } else if (e.key === 'Escape') {
      e.stopImmediatePropagation();
      hide();
    }
  }, true);

  inputEl.addEventListener('blur', () => { setTimeout(hide, 150); });
  dropdownEl.addEventListener('mousedown', e => e.preventDefault());
  dropdownEl.addEventListener('click', e => {
    const item = e.target.closest('.autocomplete-item');
    if (!item) return;
    hide();
    navigate(`/r/${item.dataset.sub}`);
  });
}

setupAutocomplete(subInput,   document.getElementById('autocomplete-dropdown'));
setupAutocomplete(pvSubInput, document.getElementById('pv-autocomplete-dropdown'));

// ── Boot ──────────────────────────────────────────────────────────────────────
renderRoute(parseRoute());
