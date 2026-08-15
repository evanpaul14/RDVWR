import { state } from './state.js';
import { settings, saveSettings, applySettings, DEFAULTS } from './settings.js';
import { clearVisited } from './visited.js';
import { _markPostVisited, applyVisitedHiding, clearVisitedHiding } from './visited-ui.js';
import { escHtml, setActiveButton, TOUCH_MOVE_THRESHOLD } from './utils.js';
import { parseRoute } from './router.js';
import { openLightbox, closeLightbox } from './lightbox.js';
import { hideAllAutocomplete, initAutocomplete } from './autocomplete.js';
import { initKeyboard } from './keyboard.js';
import {
  loadSubreddit, loadSubFeed,
  loadMultireddit, loadMultiFeed,
  loadHome, loadHomeFeed, buildHomeSortHtml,
  loadDuplicatesPage,
  sortBar, setMainOpen, buildSubSortHtml,
} from './feed.js';
import { loadProfile, loadProfileTab, buildProfileSortHtml } from './profile.js';
import { loadSearch, loadSearchResults, loadCommunityResults, loadUserResults, searchTypeBar, SEARCH_SORT_BTN_HTML } from './search.js';
import { loadWikiPage } from './wiki.js';
import { loadLiveThread, loadMoreLiveUpdates, cancelLivePoll } from './live.js';
import { loadPostView, closePostView, openPostView, changeCommentSort, loadMoreComments, stepViewFullThread } from './postview.js';
import { closeSidebar, toggleSidebar, toggleUserSidebar } from './sidebar.js';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const feed              = document.getElementById('feed');
const sentinel          = document.getElementById('scroll-sentinel');
const subInput          = document.getElementById('subreddit-input');
const pvSubInput        = document.getElementById('pv-subreddit-input');
const mobileSearchInput = document.getElementById('mobile-search-input');
const pvScroll          = document.getElementById('pv-scroll');
const postView          = document.getElementById('post-view');
const pvContent         = document.getElementById('pv-content');
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

// ── Bottom nav & mobile search ────────────────────────────────────────────────
function updateBottomNav(route) {
  const bnHome   = document.getElementById('bn-home');
  const bnSearch = document.getElementById('bn-search');
  if (!bnHome) return;
  [bnHome, bnSearch].forEach(b => b.classList.remove('active'));
  const headerSearchBtn = document.getElementById('header-search-btn');
  if (document.body.classList.contains('mobile-search-open') || route.type === 'search') {
    bnSearch?.classList.add('active');
    headerSearchBtn?.classList.add('active');
  } else {
    bnHome.classList.add('active');
    headerSearchBtn?.classList.remove('active');
  }
}

function openMobileSearch() {
  document.body.classList.add('mobile-search-open');
  document.getElementById('bn-search')?.classList.add('active');
  document.getElementById('header-search-btn')?.classList.add('active');
  document.getElementById('pv-search-toggle')?.classList.add('active');
  mobileSearchInput?.focus();
}

function closeMobileSearch() {
  document.body.classList.remove('mobile-search-open');
  document.getElementById('bn-search')?.classList.remove('active');
  document.getElementById('header-search-btn')?.classList.remove('active');
  document.getElementById('pv-search-toggle')?.classList.remove('active');
  if (mobileSearchInput) mobileSearchInput.value = '';
  hideAllAutocomplete();
}

let _isBootRender = true;

async function renderRoute(route, { restoreScroll=0, restorePvScroll=0 }={}) {
  const isBoot = _isBootRender;
  _isBootRender = false;
  closeMobileSearch();
  updateBottomNav(route);
  if (route.type !== 'search') {
    searchTypeBar.style.display = 'none';
    state.searchType = 'posts';
  }
  if (route.type !== 'home') state.homeMode = false;
  if (route.type !== 'duplicates') state.duplicatesMode = false;
  if (route.type !== 'wiki') state.wikiMode = false;
  if (route.type !== 'live') { state.liveMode = false; cancelLivePoll(); }
  switch (route.type) {
    case 'home':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      await loadHome(route.sort || 'best', route.time || 'all', route.after || null);
      break;
    case 'sub': {
      closePostView();
      closeSidebar();
      state.searchMode = false;
      const subResult = await loadSubreddit(route.sub, route.sort, route.time || 'all', route.after || null);
      if (subResult?.notFound) {
        navigate(`/search?q=${encodeURIComponent(route.sub)}&stype=communities`, { replace: true });
      }
      break;
    }
    case 'multi':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      state.profileMode = false;
      await loadMultireddit(route.username, route.multiname, route.sort, route.time || 'all', route.after || null);
      break;
    case 'post':
      if (!feed.querySelector('.post')) loadSubreddit(route.sub, state.currentSort);
      _markPostVisited(route.postId);
      await loadPostView(route.sub, route.postId, route.commentId||'', restorePvScroll, isBoot);
      break;
    case 'user':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      await loadProfile(route.username, route.after || null);
      break;
    case 'search':
      closePostView();
      closeSidebar();
      await loadSearch(route.query, route.sort, route.time, route.sub, true, route.stype || 'posts', route.after || null);
      break;
    case 'duplicates':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      state.profileMode = false;
      await loadDuplicatesPage(route.sub, route.postId, route.after || null);
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
  if (route.type === 'sub' && hasFeedPosts && route.sub === state.currentSub && !state.searchMode && !state.profileMode && !state.duplicatesMode && !state.multiMode && !state.homeMode && route.sort === state.currentSort && (route.time || 'all') === state.currentTime && (route.after || null) === state.currentAfter) {
    window.scrollTo({top: savedScroll, behavior: 'instant'});
    return;
  }
  if (route.type === 'home' && hasFeedPosts && state.homeMode && route.sort === state.currentSort && (route.time || 'all') === state.currentTime && (route.after || null) === state.currentAfter) {
    window.scrollTo({top: savedScroll, behavior: 'instant'});
    return;
  }
  if (route.type === 'multi' && hasFeedPosts && route.username === state.multiUsername && route.multiname === state.multiName && state.multiMode && route.sort === state.currentSort && (route.time || 'all') === state.currentTime && (route.after || null) === state.currentAfter) {
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

  const redditLive = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/live\/([A-Za-z0-9_-]+)/);
  if (redditLive) { e.preventDefault(); navigateOrOpen(`/live/${redditLive[1]}`, e); return true; }
  const redditPost = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/r\/([^\/]+)\/comments\/([^\/?\s#]+)(?:\/[^\/?\s#]*\/([a-z0-9]+))?/i);
  if (redditPost) { e.preventDefault(); navigateOrOpen(`/r/${redditPost[1]}/comments/${redditPost[2]}${redditPost[3] ? '/comment/' + redditPost[3] : ''}`, e); return true; }
  const redditWiki = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/r\/([^\/]+)\/wiki(?:\/([^\s#?]*))?/);
  if (redditWiki) { e.preventDefault(); navigateOrOpen(`/r/${redditWiki[1]}/wiki/${redditWiki[2]||'index'}`, e); return true; }
  const redditSub  = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/r\/([^\/?\s#]+)(\/[^?\s#]*)?/);
  if (redditSub) {
    const extra = redditSub[2] || '';
    if (!extra || /^\/(hot|new|top|rising|controversial|best|gilded)?\/?$/.test(extra)) {
      e.preventDefault(); navigateOrOpen(`/r/${redditSub[1]}`, e); return true;
    }
    // Unrecognized path (e.g. share link /s/token) — resolve the redirect then navigate
    e.preventDefault();
    fetch(`/api/resolve?url=${encodeURIComponent(href)}`)
      .then(r => r.json())
      .then(data => {
        if (!data.url) return;
        const post = data.url.match(/\/r\/([^\/]+)\/comments\/([^\/?\s#]+)(?:\/[^\/?\s#]*\/([a-z0-9]+))?/i);
        if (post) { navigate(`/r/${post[1]}/comments/${post[2]}${post[3] ? '/comment/' + post[3] : ''}`); return; }
        const user = data.url.match(/\/u(?:ser)?\/([^\/?\s#]+)/);
        if (user) { navigate(`/user/${user[1]}`); return; }
        const sub = data.url.match(/\/r\/([^\/?\s#]+)/);
        if (sub) { navigate(`/r/${sub[1]}`); return; }
        window.open(data.url, '_blank');
      })
      .catch(() => window.open(href, '_blank'));
    return true;
  }
  const redditUser = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/u(?:ser)?\/([^\/?\s#]+)/);
  if (redditUser) { e.preventDefault(); navigateOrOpen(`/user/${redditUser[1]}`, e); return true; }
  try {
    const url = new URL(href, location.origin);
    if (url.origin !== location.origin) return false;
    e.preventDefault();
    navigateOrOpen(url.pathname + url.search, e);
    return true;
  } catch { return false; }
}

// ── Retry feed load ───────────────────────────────────────────────────────────
function retryFeedLoad() {
  if (state._wikiSub && state.wikiMode) {
    loadWikiPage(state._wikiSub, state._wikiPage);
    return;
  }
  if (state.liveMode) {
    loadLiveThread(state.liveThreadId);
  } else if (state.duplicatesMode) {
    loadDuplicatesPage(state.duplicatesSub, state.duplicatesPostId);
  } else if (state.searchMode) {
    if (state.searchType === 'communities') loadCommunityResults(state.searchQuery);
    else if (state.searchType === 'users')  loadUserResults(state.searchQuery);
    else loadSearchResults(state.searchQuery, state.searchSort, state.searchTime);
  } else if (state.profileMode) {
    loadProfileTab(state.profileUser, state.profileTab, state.profileSort, state.profileTime);
  } else if (state.multiMode) {
    loadMultiFeed(state.multiUsername, state.multiName, state.currentSort, state.currentTime);
  } else if (state.homeMode) {
    loadHomeFeed(state.currentSort, state.currentTime);
  } else {
    loadSubFeed(state.currentSub, state.currentSort, state.currentTime);
  }
}

// ── Event handlers ────────────────────────────────────────────────────────────

// pv-home button
document.getElementById('pv-home').addEventListener('click', () => {
  navigate('/home');
});

// Comment collapse
document.getElementById('post-view').addEventListener('click', e => {
  const header = e.target.closest('.comment-header');
  if (!header || e.target.tagName==='A') return;
  const comment   = header.closest('.comment');
  const collapsed = comment.classList.toggle('collapsed');
  const btn = comment.querySelector(':scope > .comment-header > .comment-collapse');
  if (btn) btn.textContent = collapsed ? '+' : '−';
});

// pvContent: comment sort, load more, retry, user nav
pvContent.addEventListener('click', e => {
  const threadNavBtn = e.target.closest('[data-thread-nav]');
  if (threadNavBtn) { e.preventDefault(); stepViewFullThread(); return; }
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
});

// Feed clicks: retry
feed.addEventListener('click', e => {
  if (e.defaultPrevented) return;
  const retryBtn = e.target.closest('.state-retry-btn[data-retry]');
  if (retryBtn) { retryFeedLoad(); return; }
  const continueBtn = e.target.closest('.state-continue-btn[data-sub]');
  if (continueBtn) {
    const { sub, sort, time } = continueBtn.dataset;
    loadSubFeed(sub, sort, time, null, false, true);
    return;
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

function buildSearchUrl(q=state.searchQuery, sort=state.searchSort, time=state.searchTime, sub=state.searchSub) {
  let url = `/search?q=${encodeURIComponent(q)}&sort=${sort}`;
  if (time !== 'all') url += `&t=${time}`;
  if (sub)  url += `&sub=${encodeURIComponent(sub)}`;
  return url;
}

// Sort bar click
sortBar.addEventListener('click', e => {
  if (e.target.closest('#sidebar-toggle-btn')) {
    if (state.profileMode) toggleUserSidebar(state.profileUser);
    else toggleSidebar(state.currentSub);
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
  if (state.homeMode) {
    navigate(`/home/${state.currentSort}`, { replace:true });
  } else if (state.multiMode) {
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
  } else if (state.homeMode) {
    state.currentTime = sel.value;
    state.afterToken = null;
    window.scrollTo({top:0, behavior:'instant'});
    navigate(`/home/${state.currentSort}?t=${state.currentTime}`, { replace:true });
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
  let activeInput;
  if (e?.currentTarget?.id === 'pv-search-btn' || e?.target === pvSubInput || document.activeElement === pvSubInput) {
    activeInput = pvSubInput;
  } else if (e?.currentTarget?.id === 'mobile-search-btn' || e?.target === mobileSearchInput || document.activeElement === mobileSearchInput) {
    activeInput = mobileSearchInput;
  } else {
    activeInput = subInput;
  }
  const val = activeInput.value.trim();
  if (!val) return;
  const mMultiInput = val.match(/^u(?:ser)?\/([^\/]+)\/m\/([^\/]+)/i);
  if (mMultiInput) { navigate(`/user/${mMultiInput[1]}/m/${mMultiInput[2]}`); return; }
  if (val.startsWith('r/')) {
    const sub = val.slice(2).replace(/^\//, '');
    if (sub) navigate(`/r/${sub}/${settings.subSort}`);
  } else {
    const sub = state.searchMode ? state.searchSub : (state.currentSub || '');
    let url = `/search?q=${encodeURIComponent(val)}`;
    if (sub) url += `&sub=${encodeURIComponent(sub)}`;
    navigate(url);
  }
}
document.getElementById('search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
document.getElementById('subreddit-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') { hideAllAutocomplete(); handleSearchInput(); }
});
document.getElementById('pv-search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
document.getElementById('pv-subreddit-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') { hideAllAutocomplete(); handleSearchInput(e); }
});
document.getElementById('mobile-search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
mobileSearchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') { hideAllAutocomplete(); handleSearchInput(e); }
  if (e.key === 'Escape') { closeMobileSearch(); }
});
mobileSearchInput.addEventListener('blur', () => {
  setTimeout(() => {
    if (!document.activeElement?.closest('#mobile-search-bar')) closeMobileSearch();
  }, 200);
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
  } else if (state.homeMode) {
    if (state.afterToken) loadHomeFeed(state.currentSort, state.currentTime, state.afterToken, true);
  } else {
    if (state.afterToken) loadSubFeed(state.currentSub, state.currentSort, state.currentTime, state.afterToken, true);
  }
}
function buildNextPageUrl() {
  const curPage = parseInt(new URLSearchParams(location.search).get('page')) || 1;
  const nextPage = curPage + 1;
  if (state.searchMode) {
    const after = state.searchType === 'communities' ? state.communityAfter
                : state.searchType === 'users'       ? state.userAfter
                : state.searchAfter;
    if (!after) return null;
    let url = `/search?q=${encodeURIComponent(state.searchQuery)}&sort=${state.searchSort}`;
    if (state.searchTime !== 'all') url += `&t=${state.searchTime}`;
    if (state.searchSub) url += `&sub=${encodeURIComponent(state.searchSub)}`;
    url += `&stype=${state.searchType}&after=${encodeURIComponent(after)}&page=${nextPage}`;
    return url;
  } else if (state.profileMode) {
    if (!state.profileAfter) return null;
    return `/user/${encodeURIComponent(state.profileUser)}?after=${encodeURIComponent(state.profileAfter)}&page=${nextPage}`;
  } else if (state.duplicatesMode) {
    if (!state.duplicatesAfter) return null;
    return `/r/${encodeURIComponent(state.duplicatesSub)}/duplicates/${encodeURIComponent(state.duplicatesPostId)}?after=${encodeURIComponent(state.duplicatesAfter)}&page=${nextPage}`;
  } else if (state.homeMode) {
    if (!state.afterToken) return null;
    const params = [];
    if (state.currentSort === 'top' || state.currentSort === 'controversial') params.push(`t=${state.currentTime}`);
    params.push(`after=${encodeURIComponent(state.afterToken)}`, `page=${nextPage}`);
    return `/home/${state.currentSort}?${params.join('&')}`;
  } else if (state.multiMode) {
    if (!state.afterToken) return null;
    const params = [];
    if (state.currentSort === 'top' || state.currentSort === 'controversial') params.push(`t=${state.currentTime}`);
    params.push(`after=${encodeURIComponent(state.afterToken)}`, `page=${nextPage}`);
    return `/user/${encodeURIComponent(state.multiUsername)}/m/${encodeURIComponent(state.multiName)}/${state.currentSort}?${params.join('&')}`;
  } else {
    if (!state.afterToken) return null;
    const params = [];
    if (state.currentSort === 'top' || state.currentSort === 'controversial') params.push(`t=${state.currentTime}`);
    params.push(`after=${encodeURIComponent(state.afterToken)}`, `page=${nextPage}`);
    return `/r/${encodeURIComponent(state.currentSub)}/${state.currentSort}?${params.join('&')}`;
  }
}

new IntersectionObserver(entries => {
  if (!entries[0].isIntersecting) return;
  if (settings.pagination) {
    if (sentinel.querySelector('.pagination-bar') || state.loading) return;
    const curPage = parseInt(new URLSearchParams(location.search).get('page')) || 1;
    const hasNext = !!(state.afterToken || state.searchAfter || state.profileAfter ||
                    state.duplicatesAfter || state.communityAfter || state.userAfter);
    if (!hasNext && curPage <= 1) return;
    const bar = document.createElement('div');
    bar.className = 'pagination-bar';
    if (curPage > 1) bar.innerHTML += `<button class="pagination-btn prev-page-btn">← Previous</button>`;
    if (hasNext) bar.innerHTML += `<button class="pagination-btn next-page-btn">Next page →</button>`;
    sentinel.appendChild(bar);
  } else {
    loadMore();
  }
}, { rootMargin: '300px' }).observe(sentinel);

sentinel.addEventListener('click', e => {
  if (e.target.closest('.next-page-btn')) {
    const url = buildNextPageUrl();
    if (url) { sentinel.innerHTML = ''; navigate(url); }
    return;
  }
  if (e.target.closest('.prev-page-btn')) {
    sentinel.innerHTML = '';
    history.back();
  }
});

// Spoiler reveal — delegated so it works in feed excerpts, post view body, and comments
document.addEventListener('click', e => {
  const spoiler = e.target.closest('.spoiler');
  if (!spoiler) return;
  e.stopPropagation();
  spoiler.classList.toggle('revealed');
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const spoiler = e.target.closest('.spoiler');
  if (!spoiler) return;
  e.preventDefault();
  spoiler.classList.toggle('revealed');
});

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
  const card = e.target.closest('.community-card[data-nav]');
  if (card) { navigateOrOpen(card.dataset.nav, e); return; }
  const commentCard = e.target.closest('.user-comment-card[data-nav]');
  if (commentCard && !e.target.closest('a')) { navigateOrOpen(commentCard.dataset.nav, e); return; }
});
feed.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const card = e.target.closest('.community-card[data-nav]');
  if (card) { e.preventDefault(); navigateOrOpen(card.dataset.nav, e); return; }
  const commentCard = e.target.closest('.user-comment-card[data-nav]');
  if (commentCard) { e.preventDefault(); navigateOrOpen(commentCard.dataset.nav, e); }
});

// Logo
document.getElementById('logo-btn').addEventListener('click', () => navigate('/'));
document.getElementById('popular-btn').addEventListener('click', () => navigate('/r/popular'));

// Bottom nav
document.getElementById('bn-home').addEventListener('click', () => navigate('/'));
document.getElementById('bn-search').addEventListener('click', () => {
  if (document.body.classList.contains('mobile-search-open')) {
    closeMobileSearch();
    updateBottomNav(parseRoute());
  } else {
    openMobileSearch();
  }
});
document.getElementById('bn-settings').addEventListener('click', openSettingsPanel);

// Post-view search toggle (mobile)
document.getElementById('pv-search-toggle')?.addEventListener('click', () => {
  if (document.body.classList.contains('mobile-search-open')) {
    closeMobileSearch();
  } else {
    openMobileSearch();
  }
});

// Header search button (mobile)
document.getElementById('header-search-btn').addEventListener('click', () => {
  if (document.body.classList.contains('mobile-search-open')) {
    closeMobileSearch();
    updateBottomNav(parseRoute());
  } else {
    openMobileSearch();
  }
});

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
  // Leave edge touches alone so the browser's back/forward swipe gesture can fire
  if (_touchStartX < 20 || _touchStartX > window.innerWidth - 20) return;
  const dx = Math.abs(e.changedTouches[0].clientX - _touchStartX);
  const dy = Math.abs(e.changedTouches[0].clientY - _touchStartY);
  if (dx > TOUCH_MOVE_THRESHOLD || dy > TOUCH_MOVE_THRESHOLD) return;
  if (e.target.tagName === 'IMG' && e.target.closest('.md, .pv-media, .post-media')) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank') return;
  if (interceptNavLink(a, e)) _navFromTouch = true;
}, { passive: false });

// Capture-phase click handler
document.addEventListener('click', e => {
  if (_navFromTouch) { _navFromTouch = false; return; }
  if (e.target.tagName === 'IMG' && e.target.closest('.md, .pv-media, .post-media')) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank' || a.hasAttribute('download')) return;
  interceptNavLink(a, e);
}, true);

// Middle-click
document.addEventListener('auxclick', e => {
  if (e.button !== 1) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank' || a.hasAttribute('download')) return;
  interceptNavLink(a, e);
}, true);

// Share / copy link
document.addEventListener('click', e => {
  const btn = e.target.closest('.share-btn[data-share]');
  if (!btn) return;
  e.stopPropagation();
  const url = 'https://www.reddit.com' + btn.dataset.share;
  const flash = () => {
    const prev = btn.innerHTML;
    btn.innerHTML = '✓ copied';
    btn.classList.add('share-copied');
    setTimeout(() => { btn.innerHTML = prev; btn.classList.remove('share-copied'); }, 1500);
  };
  if (navigator.clipboard) {
    navigator.clipboard.writeText(url).then(flash).catch(() => {});
  } else {
    const ta = Object.assign(document.createElement('textarea'), { value: url });
    ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    try { document.execCommand('copy'); flash(); } catch {}
    ta.remove();
  }
});


document.addEventListener('click', e => {
  const img = e.target.closest('.post-media img, .pv-media img, .md img, .gallery-main-img');
  if (!img) return;
  e.preventDefault();
  e.stopPropagation();
  openLightbox(img.src);
});

// ── Platform detection ────────────────────────────────────────────────────────
let _platformPromise = null;
function getPlatform() {
  if (!_platformPromise) _platformPromise = fetch('/api/platform').then(r => r.json()).catch(() => ({}));
  return _platformPromise;
}

// ── Settings panel ────────────────────────────────────────────────────────────
const settingsPanel   = document.getElementById('settings-panel');
const settingsOverlay = document.getElementById('settings-overlay');
const settingsBody    = document.getElementById('settings-body');

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
    <label class="settings-row settings-row--stack"><span class="settings-label">Reddit cookies <span class="settings-hint">(for personalised home feed — open reddit.com, F12 → Application → Cookies → right-click the <code>reddit.com</code> row → Copy all as header value, then paste below)</span></span><textarea class="settings-input settings-textarea" id="s-reddit-cookies" spellcheck="false" autocomplete="off" placeholder="loid=…; token_v2=…; session_tracker=…">${escHtml(settings.redditCookies || '')}</textarea></label>
  </div>
  <div class="settings-section">
    <div class="settings-section-title">Comments</div>
    <label class="settings-row"><span class="settings-label">Default sort</span>${sel('s-comment-sort', csortOpts, settings.commentSort)}</label>
  </div>
  <div class="settings-section">
    <div class="settings-section-title">NSFW</div>
    <label class="settings-row"><span class="settings-label">Blur NSFW thumbnails</span>${chk('s-nsfw-blur', settings.nsfwBlur)}</label>
    <label class="settings-row"><span class="settings-label">Hide NSFW posts</span>${chk('s-nsfw-hide', settings.nsfwHide)}</label>
    <label class="settings-row"><span class="settings-label">Hide NSFW content in search</span>${chk('s-nsfw-search-hide', settings.nsfwSearchHide)}</label>
  </div>
  <div class="settings-section">
    <div class="settings-section-title">Read history</div>
    <label class="settings-row"><span class="settings-label">Mark posts as read on scroll</span>${chk('s-mark-read', settings.markRead)}</label>
    <label class="settings-row"><span class="settings-label">Hide read posts (home feed)</span>${chk('s-hide-read-home', settings.hideReadHome)}</label>
    <label class="settings-row"><span class="settings-label">Hide read posts (subreddits)</span>${chk('s-hide-read-sub', settings.hideReadSub)}</label>
    <div class="settings-row settings-row-action"><span class="settings-label">Clear read history</span><button class="settings-action-btn" id="s-clear-visited">Clear</button></div>
  </div>
  <div class="settings-section">
    <button class="settings-reset-btn" id="s-reset">Reset to defaults</button>
  </div>`
  + (window._androidUpdate ? `
  <div class="settings-section" id="s-update-section">
    <div class="settings-section-title">App</div>
    <div class="settings-row settings-row-action">
      <span class="settings-label">Check for updates</span>
      <button class="settings-action-btn" id="s-update-btn">Check</button>
    </div>
    <div id="s-update-status" class="settings-update-status"></div>
  </div>` : '');
}

function openSettingsPanel() {
  settingsPanel.classList.add('open');
  settingsOverlay.classList.add('open');
  document.body.style.overflow = 'hidden';
  settingsBody.innerHTML = _settingsHtml();
  bindSettingEvents();
  if (window._androidUpdate) bindUpdateEvents();
  getPlatform().then(p => {
    if (p.android && !window._androidUpdate) {
      window._androidUpdate = true;
      settingsBody.innerHTML = _settingsHtml();
      bindSettingEvents();
      bindUpdateEvents();
    }
  });
}

function bindSettingEvents() {
  settingsBody.querySelector('#s-theme').addEventListener('change', e => { settings.theme = e.target.value; saveSettings(); });
  settingsBody.querySelector('#s-layout').addEventListener('change', e => { settings.layout = e.target.value; saveSettings(); retryFeedLoad(); });
  settingsBody.querySelector('#s-sub-sort').addEventListener('change', e => { settings.subSort = e.target.value; saveSettings(); });
  settingsBody.querySelector('#s-sub-time').addEventListener('change', e => { settings.subTime = e.target.value; saveSettings(); });
  settingsBody.querySelector('#s-reddit-cookies').addEventListener('change', e => { settings.redditCookies = e.target.value.trim(); saveSettings(); updatePopularBtn(); });
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
  settingsBody.querySelector('#s-nsfw-blur').addEventListener('change', e => { settings.nsfwBlur = e.target.checked; saveSettings(); });
  settingsBody.querySelector('#s-nsfw-hide').addEventListener('change', e => { settings.nsfwHide = e.target.checked; saveSettings(); });
  settingsBody.querySelector('#s-nsfw-search-hide').addEventListener('change', e => { settings.nsfwSearchHide = e.target.checked; saveSettings(); });
  settingsBody.querySelector('#s-mark-read').addEventListener('change', e => { settings.markRead = e.target.checked; saveSettings(); });
  settingsBody.querySelector('#s-hide-read-home').addEventListener('change', e => { settings.hideReadHome = e.target.checked; saveSettings(); applyVisitedHiding(); });
  settingsBody.querySelector('#s-hide-read-sub').addEventListener('change', e => { settings.hideReadSub = e.target.checked; saveSettings(); applyVisitedHiding(); });
  settingsBody.querySelector('#s-clear-visited').addEventListener('click', () => {
    clearVisited();
    clearVisitedHiding();
  });
  settingsBody.querySelector('#s-reset').addEventListener('click', () => {
    Object.assign(settings, DEFAULTS);
    state.currentCommentSort = DEFAULTS.commentSort;
    saveSettings();
    settingsBody.innerHTML = _settingsHtml();
    bindSettingEvents();
    if (window._androidUpdate) bindUpdateEvents();
  });
}

function bindUpdateEvents() {
  const btn = settingsBody.querySelector('#s-update-btn');
  const statusEl = settingsBody.querySelector('#s-update-status');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = '…';
    statusEl.textContent = '';
    statusEl.className = 'settings-update-status';
    try {
      const res = await fetch('/api/update', { method: 'POST' });
      const data = await res.json();
      if (data.status === 'updated') {
        statusEl.textContent = `Updated (${data.changed.length} file${data.changed.length !== 1 ? 's' : ''})`;
        statusEl.className = 'settings-update-status settings-update-ok';
        btn.textContent = 'Restart';
        btn.disabled = false;
        btn.addEventListener('click', () => { window.location.href = 'rdvwr://restart'; }, { once: true });
      } else if (data.status === 'up_to_date') {
        statusEl.textContent = 'Already up to date';
        statusEl.className = 'settings-update-status settings-update-ok';
        btn.textContent = 'Check';
        btn.disabled = false;
      } else if (data.status === 'unsupported') {
        statusEl.textContent = 'Updates not supported on this platform';
        statusEl.className = 'settings-update-status settings-update-err';
        btn.textContent = 'Check';
        btn.disabled = false;
      } else {
        statusEl.textContent = data.message || 'Update failed';
        statusEl.className = 'settings-update-status settings-update-err';
        btn.textContent = 'Retry';
        btn.disabled = false;
      }
    } catch (e) {
      statusEl.textContent = 'Network error';
      statusEl.className = 'settings-update-status settings-update-err';
      btn.textContent = 'Retry';
      btn.disabled = false;
    }
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

// ── Popular button visibility ─────────────────────────────────────────────────
function updatePopularBtn() {
  const btn = document.getElementById('popular-btn');
  if (btn) btn.hidden = !settings.redditCookies;
}

// ── Boot ──────────────────────────────────────────────────────────────────────
applySettings();
state.currentCommentSort = settings.commentSort;
updatePopularBtn();
initAutocomplete(subInput, pvSubInput, navigate, mobileSearchInput);
initKeyboard({ navigate, feed, pvContent, postView, subInput, settingsPanel, closeSettingsPanel, closeLightbox, refreshFeed: retryFeedLoad });
renderRoute(parseRoute());

// Retry home feed when app returns to foreground with no posts loaded
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  if (state.homeMode && !feed.querySelector('.post') && !state.loading) {
    loadHomeFeed(state.currentSort || 'best', state.currentTime || 'all');
  }
});
