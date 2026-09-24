import { state } from './state.js';
import { settings, saveSettings, applySettings, DEFAULTS } from './settings.js';
import { clearVisited } from './visited.js';
import { _markPostVisited, clearVisitedHiding } from './visited-ui.js';
import { escHtml, setActiveButton, TOUCH_MOVE_THRESHOLD } from './utils.js';
import { parseRoute } from './router.js';
import { openLightbox, closeLightbox } from './lightbox.js';
import { hideAllAutocomplete, initAutocomplete } from './autocomplete.js';
import { initKeyboard } from './keyboard.js';
import { initCardHibernation, cardContent } from './hibernate.js';
import { toggleSaved, saveBtnLabel } from './saved.js';
import { getSubs, isSubscribed, isSubscribable, toggleSub, MAX_SUBS } from './subscriptions.js';
import {
  loadSubreddit, loadSubFeed,
  loadMultireddit, loadMultiFeed,
  loadHome, loadHomeFeed,
  loadDuplicatesPage, loadSaved, loadSubscribed,
  sortBar,
} from './feed.js';
import { loadProfile, loadProfileTab, buildProfileSortHtml } from './profile.js';
import { loadSearch, loadSearchResults, loadCommunityResults, loadUserResults, searchTypeBar } from './search.js';
import { loadWikiPage } from './wiki.js';
import { loadLiveThread, loadMoreLiveUpdates, cancelLivePoll } from './live.js';
import { loadPostView, closePostView, changeCommentSort, loadMoreComments, stepViewFullThread } from './postview.js';
import { closeSidebar, toggleSidebar, toggleUserSidebar } from './sidebar.js';

// Echo the rdvwr_csrf cookie as a header: the /api/* gate's fallback for browsers that
// strip Sec-Fetch-Site/Origin/Referer (see helpers.is_same_site_request).
(() => {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const m = document.cookie.match(/(?:^|; )rdvwr_csrf=([^;]+)/);
    if (m) init = { ...init, headers: { ...(init.headers || {}), 'X-Rdvwr-Fetch': m[1] } };
    return nativeFetch(input, init);
  };
})();

// ── DOM refs ──────────────────────────────────────────────────────────────────
const feed              = document.getElementById('feed');
const sentinel          = document.getElementById('scroll-sentinel');
const subInput          = document.getElementById('subreddit-input');
const pvSubInput        = document.getElementById('pv-subreddit-input');
const mobileSearchInput = document.getElementById('mobile-search-input');
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
  const bnSaved  = document.getElementById('bn-saved');
  if (!bnHome) return;
  [bnHome, bnSearch, bnSaved].forEach(b => b?.classList.remove('active'));
  const headerSearchBtn = document.getElementById('header-search-btn');
  if (document.body.classList.contains('mobile-search-open') || route.type === 'search') {
    bnSearch?.classList.add('active');
    headerSearchBtn?.classList.add('active');
  } else if (route.type === 'saved') {
    bnSaved?.classList.add('active');
    headerSearchBtn?.classList.remove('active');
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
  if (route.type !== 'saved') state.savedMode = false;
  if (!['home', 'subscribed', 'post'].includes(route.type)) state.subsMode = false;
  if (route.type !== 'post') { updateSubscribeBtn(route); updateFeedsActive(route); }
  if (route.type !== 'live') { state.liveMode = false; cancelLivePoll(); }
  if (route.type !== 'post') {
    closePostView();
    closeSidebar();
    if (route.type !== 'search') state.searchMode = false;
  }
  switch (route.type) {
    case 'home':
      if (subscribedIsHome()) {
        await loadSubscribed(route.sort || 'best', route.time || 'all', route.after || null, '/home');
      } else {
        state.subsMode = false;
        await loadHome(route.sort || 'best', route.time || 'all', route.after || null);
      }
      break;
    case 'subscribed':
      await loadSubscribed(route.sort, route.time || 'all', route.after || null, '/subscribed');
      break;
    case 'sub': {
      const subResult = await loadSubreddit(route.sub, route.sort, route.time || 'all', route.after || null);
      if (subResult?.notFound) {
        navigate(`/search?q=${encodeURIComponent(route.sub)}&stype=communities`, { replace: true });
      }
      break;
    }
    case 'multi':
      state.profileMode = false;
      await loadMultireddit(route.username, route.multiname, route.sort, route.time || 'all', route.after || null);
      break;
    case 'post':
      if (!feed.querySelector('.post')) loadSubreddit(route.sub, state.currentSort);
      _markPostVisited(route.postId);
      await loadPostView(route.sub, route.postId, route.commentId||'', restorePvScroll, isBoot);
      break;
    case 'user':
      await loadProfile(route.username, route.after || null);
      break;
    case 'search':
      await loadSearch(route.query, route.sort, route.time, route.sub, true, route.stype || 'posts', route.after || null);
      break;
    case 'duplicates':
      state.profileMode = false;
      await loadDuplicatesPage(route.sub, route.postId, route.after || null);
      break;
    case 'wiki':
      state.profileMode = false;
      await loadWikiPage(route.sub, route.page);
      break;
    case 'saved':
      state.duplicatesMode = false;
      await loadSaved();
      break;
    case 'live':
      state.profileMode = false;
      await loadLiveThread(route.threadId);
      break;
  }
  if (route.type !== 'post') window.scrollTo({top: restoreScroll, behavior: 'instant'});
}

// Whether the feed on screen is already `route`, so going back only restores scroll.
function isFeedShowing(route) {
  const samePage = sort => sort === state.currentSort
    && (route.time || 'all') === state.currentTime && (route.after || null) === state.currentAfter;
  switch (route.type) {
    case 'sub':
      return route.sub === state.currentSub && !state.searchMode && !state.profileMode && !state.duplicatesMode
        && !state.multiMode && !state.homeMode && samePage(route.sort);
    case 'home':
    case 'subscribed':
      if (state.subsMode && state.subsBase === `/${route.type}` && samePage(route.sort || 'best')) return true;
      return route.type === 'home' && state.homeMode && samePage(route.sort);
    case 'multi':
      return state.multiMode && route.username === state.multiUsername && route.multiname === state.multiName
        && samePage(route.sort);
  }
  return false;
}

window.addEventListener('popstate', (e) => {
  const savedScroll = e.state?.scrollY || 0;
  const savedPvScroll = e.state?.pvScrollTop || 0;
  const route = parseRoute();
  if (route.type !== 'post') closePostView();
  if (feed.querySelector('.post') && isFeedShowing(route)) {
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
    // Unrecognized path (e.g. a /s/ share link): resolve its redirect first.
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
  if (state.savedMode) {
    loadSaved();
  } else if (state.subsMode) {
    loadSubscribed(state.currentSort, state.currentTime, null, state.subsBase);
  } else if (state.liveMode) {
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
  if (handleFlairClick(e)) return;
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

// Path of the current sub/home/subscribed/multi listing, without its sort.
function feedBasePath(enc = s => s) {
  if (state.subsMode)  return state.subsBase;
  if (state.homeMode)  return '/home';
  if (state.multiMode) return `/user/${enc(state.multiUsername)}/m/${enc(state.multiName)}`;
  return `/r/${enc(state.currentSub)}`;
}

function resetFeedScroll() {
  state.afterToken = null;
  window.scrollTo({top:0, behavior:'instant'});
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
  resetFeedScroll();
  navigate(`${feedBasePath()}/${state.currentSort}`, { replace:true });
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
  } else {
    state.currentTime = sel.value;
    resetFeedScroll();
    navigate(`${feedBasePath()}/${state.currentSort}?t=${state.currentTime}`, { replace:true });
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
function submitSearch(e) { hideAllAutocomplete(); handleSearchInput(e); }
for (const [btnId, input] of [['search-btn', subInput], ['pv-search-btn', pvSubInput], ['mobile-search-btn', mobileSearchInput]]) {
  document.getElementById(btnId).addEventListener('click', submitSearch);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') submitSearch(e); });
}
mobileSearchInput.addEventListener('keydown', e => { if (e.key === 'Escape') closeMobileSearch(); });
mobileSearchInput.addEventListener('blur', () => {
  setTimeout(() => {
    if (!document.activeElement?.closest('#mobile-search-bar')) closeMobileSearch();
  }, 200);
});

// Infinite scroll
function loadMore() {
  if (state.loading || state.wikiMode || state.savedMode) return;
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
    return `${buildSearchUrl()}&stype=${state.searchType}&after=${encodeURIComponent(after)}&page=${nextPage}`;
  } else if (state.profileMode) {
    if (!state.profileAfter) return null;
    return `/user/${encodeURIComponent(state.profileUser)}?after=${encodeURIComponent(state.profileAfter)}&page=${nextPage}`;
  } else if (state.duplicatesMode) {
    if (!state.duplicatesAfter) return null;
    return `/r/${encodeURIComponent(state.duplicatesSub)}/duplicates/${encodeURIComponent(state.duplicatesPostId)}?after=${encodeURIComponent(state.duplicatesAfter)}&page=${nextPage}`;
  }
  if (!state.afterToken) return null;
  const params = [];
  if (state.currentSort === 'top' || state.currentSort === 'controversial') params.push(`t=${state.currentTime}`);
  params.push(`after=${encodeURIComponent(state.afterToken)}`, `page=${nextPage}`);
  return `${feedBasePath(encodeURIComponent)}/${state.currentSort}?${params.join('&')}`;
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

// Spoiler reveal, delegated so it works everywhere
document.addEventListener('click', e => {
  const spoiler = e.target.closest('.spoiler');
  if (!spoiler) return;
  if (e.target.closest('a')) return; // let links inside a revealed spoiler work
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
function handleFlairClick(e) {
  const flairEl = e.target.closest('.flair.flair-clickable[data-flair]');
  if (!flairEl) return false;
  e.stopPropagation();
  const sub   = flairEl.dataset.sub;
  const flair = flairEl.dataset.flair;
  if (sub && flair) {
    const query = 'flair:"'+flair+'"';
    const titleHtml = flairEl.outerHTML
      .replace(' flair-clickable', '')
      .replace(/ data-flair="[^"]*"/, '')
      .replace(/ data-sub="[^"]*"/, '');
    state.searchFlairNav = { query, html: titleHtml };
    navigateOrOpen(`/search?q=${encodeURIComponent(query)}&sub=${encodeURIComponent(sub)}&sort=new`, e);
  }
  return true;
}
feed.addEventListener('click', e => {
  if (handleFlairClick(e)) return;
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

// Feeds menu: a plain "saved" link, or a saved/popular dropdown when cookies enable popular
const feedsBtn      = document.getElementById('feeds-btn');
const feedsDropdown = document.getElementById('feeds-dropdown');
function setFeedsMenuOpen(open) {
  feedsDropdown.hidden = !open;
  feedsBtn.setAttribute('aria-expanded', String(open));
}
feedsBtn.addEventListener('click', e => {
  e.stopPropagation();
  if (!settings.redditCookies) { navigate('/saved'); return; }
  setFeedsMenuOpen(feedsDropdown.hidden);
});
feedsDropdown.addEventListener('click', e => {
  const item = e.target.closest('.feeds-item[data-feed]');
  if (!item) return;
  setFeedsMenuOpen(false);
  navigate(item.dataset.feed);
});
document.addEventListener('click', e => {
  if (!feedsDropdown.hidden && !e.target.closest('#feeds-menu')) setFeedsMenuOpen(false);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !feedsDropdown.hidden) setFeedsMenuOpen(false);
});

// Subscribed-feed subreddit list: close on outside click, Escape, or picking a subreddit
document.addEventListener('click', e => {
  const open = document.querySelector('.ctx-subs[open]');
  if (open && (!open.contains(e.target) || e.target.closest('.ctx-subs-item'))) open.open = false;
});
document.addEventListener('keydown', e => {
  const open = document.querySelector('.ctx-subs[open]');
  if (e.key === 'Escape' && open) open.open = false;
});

// Mobile search toggles (bottom nav, header, post view)
function toggleMobileSearch() {
  if (!document.body.classList.contains('mobile-search-open')) { openMobileSearch(); return; }
  closeMobileSearch();
  updateBottomNav(parseRoute());
}
document.getElementById('bn-home').addEventListener('click', () => navigate('/'));
document.getElementById('bn-search').addEventListener('click', toggleMobileSearch);
document.getElementById('bn-saved').addEventListener('click', () => navigate('/saved'));
document.getElementById('bn-settings').addEventListener('click', openSettingsPanel);
document.getElementById('pv-search-toggle')?.addEventListener('click', toggleMobileSearch);
document.getElementById('header-search-btn').addEventListener('click', toggleMobileSearch);

// Long-press on post card → open in new tab (mobile)
let _longPressTimer = null;
document.addEventListener('touchstart', e => {
  const post = e.target.closest('#feed .post, #feed .post-compact');
  if (!post || e.target.closest('a, button, video, iframe, input')) return;
  const titleLink = post.querySelector('a[data-nav]');
  if (!titleLink) return;
  _longPressTimer = setTimeout(() => {
    _longPressTimer = null;
    if (navigator.vibrate) navigator.vibrate(40);
    window.open(titleLink.dataset.nav, '_blank');
  }, 550);
}, { passive: true });
function cancelLongPress() {
  if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
}
document.addEventListener('touchmove', cancelLongPress, { passive: true });
document.addEventListener('touchend', cancelLongPress, { passive: true });

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
  if (e.target.closest('.spoiler:not(.revealed)')) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank') return;
  if (a.classList.contains('thumb-lightbox') && a.hasAttribute('data-lightbox')) return;
  if (interceptNavLink(a, e)) _navFromTouch = true;
}, { passive: false });

// Capture-phase click handler
document.addEventListener('click', e => {
  if (_navFromTouch) { _navFromTouch = false; return; }
  if (e.target.tagName === 'IMG' && e.target.closest('.md, .pv-media, .post-media')) return;
  const unrevealedSpoiler = e.target.closest('.spoiler:not(.revealed)');
  if (unrevealedSpoiler) { e.preventDefault(); e.stopPropagation(); unrevealedSpoiler.classList.add('revealed'); return; }
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank' || a.hasAttribute('download')) return;
  if (a.classList.contains('thumb-lightbox') && a.hasAttribute('data-lightbox')) return;
  interceptNavLink(a, e);
}, true);

// Spoiler/NSFW veils; capture phase so the reveal beats bubble-phase card handlers.
document.addEventListener('click', e => {
  const veil = e.target.closest('.spoiler-veil, .nsfw-veil');
  if (!veil) return;
  e.preventDefault();
  veil.parentElement.classList.add('revealed');
}, true);
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const veil = e.target.closest?.('.spoiler-veil, .nsfw-veil');
  if (!veil) return;
  e.preventDefault();
  veil.parentElement.classList.add('revealed');
}, true);

// Middle-click
document.addEventListener('auxclick', e => {
  if (e.button !== 1) return;
  if (e.target.closest('.spoiler:not(.revealed)')) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank' || a.hasAttribute('download')) return;
  interceptNavLink(a, e);
}, true);

// Subscribe / unsubscribe (local)
const ctxSubBtn = document.getElementById('ctx-sub-btn');
function _renderSubscribeBtn(sub) {
  const on = isSubscribed(sub);
  ctxSubBtn.textContent = on ? 'subscribed' : 'subscribe';
  ctxSubBtn.classList.toggle('is-subscribed', on);
  ctxSubBtn.setAttribute('aria-pressed', String(on));
  ctxSubBtn.title = on ? `Unsubscribe from r/${sub}` : `Add r/${sub} to your subscribed feed`;
}
function updateSubscribeBtn(route) {
  const show = route.type === 'sub' && isSubscribable(route.sub);
  ctxSubBtn.hidden = !show;
  if (!show) return;
  ctxSubBtn.dataset.sub = route.sub;
  _renderSubscribeBtn(route.sub);
}
ctxSubBtn.addEventListener('click', () => {
  const sub = ctxSubBtn.dataset.sub;
  if (!sub) return;
  if (toggleSub(sub) === null) {
    ctxSubBtn.title = getSubs().length >= MAX_SUBS
      ? `You can subscribe to at most ${MAX_SUBS} subreddits`
      : 'Could not save (storage full or unavailable)';
    return;
  }
  _renderSubscribeBtn(sub);
});

// Save / unsave (local)
function _setSaveBtn(btn, saved) {
  btn.classList.toggle('is-saved', saved);
  btn.setAttribute('aria-pressed', String(saved));
  btn.title = saved ? 'Remove from saved' : 'Save post';
  btn.setAttribute('aria-label', btn.title);
  btn.innerHTML = saveBtnLabel(saved, btn.dataset.minimal === '1');
}

document.addEventListener('click', e => {
  const btn = e.target.closest('.save-btn[data-save]');
  if (!btn) return;
  e.stopPropagation();
  const id = btn.dataset.save;
  const saved = toggleSaved(id);
  if (saved === null) {
    btn.title = 'Could not save (storage full or unavailable)';
    return;
  }
  const sel = `.save-btn[data-save="${CSS.escape(id)}"]`;
  document.querySelectorAll(sel).forEach(b => _setSaveBtn(b, saved));
  // A hibernated feed card holds its buttons outside the document.
  const card = feed.querySelector(`[data-post-id="${CSS.escape(id)}"]`);
  if (card) cardContent(card).querySelectorAll(sel).forEach(b => _setSaveBtn(b, saved));
});

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
  openLightbox(img.dataset.full || img.src);
});

document.addEventListener('click', e => {
  if (e.target.closest('.spoiler-veil, .nsfw-veil')) return;
  const thumb = e.target.closest('.thumb-lightbox[data-lightbox]');
  if (!thumb) return;
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  e.stopPropagation();
  if (thumb.dataset.gallery) {
    try { openLightbox(JSON.parse(thumb.dataset.gallery), 0); return; } catch {}
  }
  openLightbox(thumb.dataset.lightbox);
});

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
    ${window.__DISABLE_PERSONALIZED_HOME__ ? '' : `
    <label class="settings-row" id="s-home-feed-row"${settings.redditCookies ? '' : ' style="display:none"'}><span class="settings-label">Home feed</span>${sel('s-home-feed', [['personalized','Personalized'],['subscribed','Subscribed']], settings.homeFeed || 'personalized')}</label>
    <label class="settings-row settings-row--stack"><span class="settings-label">Reddit cookies <span class="settings-hint">(for personalised home feed — open reddit.com, F12 → Application → Cookies → right-click the <code>reddit.com</code> row → Copy all as header value, then paste below)</span></span><textarea class="settings-input settings-textarea" id="s-reddit-cookies" spellcheck="false" autocomplete="off" placeholder="loid=…; token_v2=…; session_tracker=…">${escHtml(settings.redditCookies || '')}</textarea></label>`}
  </div>
  <div class="settings-section">
    <div class="settings-section-title">Comments</div>
    <label class="settings-row"><span class="settings-label">Default sort</span>${sel('s-comment-sort', csortOpts, settings.commentSort)}</label>
    <label class="settings-row"><span class="settings-label">Show profile pictures</span>${chk('s-show-avatars', settings.showAvatars)}</label>
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
  </div>
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
  // Save input `id` into settings[key], then run `after`.
  const bind = (id, key, after) => settingsBody.querySelector(id)?.addEventListener('change', e => {
    const el = e.target;
    settings[key] = el.type === 'checkbox' ? el.checked : el.value.trim();
    saveSettings();
    after?.(settings[key]);
  });
  bind('#s-theme', 'theme');
  bind('#s-layout', 'layout', retryFeedLoad);
  bind('#s-sub-sort', 'subSort');
  bind('#s-sub-time', 'subTime');
  bind('#s-reddit-cookies', 'redditCookies', cookies => {
    updateFeedsBtn();
    settingsBody.querySelector('#s-home-feed-row').style.display = cookies ? '' : 'none';
  });
  bind('#s-home-feed', 'homeFeed', () => {
    updateFeedsBtn();
    if (parseRoute().type === 'home') renderRoute(parseRoute());
  });
  bind('#s-comment-sort', 'commentSort', sort => { state.currentCommentSort = sort; });
  bind('#s-pagination', 'pagination', on => { if (!on) sentinel.innerHTML = ''; });
  bind('#s-show-avatars', 'showAvatars', () => {
    if (postView.classList.contains('open')) changeCommentSort(state.currentCommentSort);
  });
  bind('#s-link-external-media', 'linkExternalMedia', retryFeedLoad);
  bind('#s-nsfw-blur', 'nsfwBlur');
  bind('#s-nsfw-hide', 'nsfwHide');
  bind('#s-nsfw-search-hide', 'nsfwSearchHide');
  bind('#s-mark-read', 'markRead');
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

// ── Feeds button mode ─────────────────────────────────────────────────────────
/** Local subscriptions take over Home when there's no personalized feed, or when the
 *  user picked them as the home feed in settings. */
function subscribedIsHome() {
  return getSubs().length > 0 && (!personalizedHomeActive() || settings.homeFeed === 'subscribed');
}

function personalizedHomeActive() {
  return !window.__DISABLE_PERSONALIZED_HOME__ && !!settings.redditCookies;
}

function updateFeedsBtn() {
  const menu = personalizedHomeActive();
  document.getElementById('feeds-menu').classList.toggle('has-menu', menu);
  feedsBtn.textContent = menu ? 'feeds ▾' : 'saved';
  feedsBtn.setAttribute('aria-label', menu ? 'Feeds' : 'Saved posts');
  if (menu) feedsBtn.setAttribute('aria-haspopup', 'menu');
  else { feedsBtn.removeAttribute('aria-haspopup'); setFeedsMenuOpen(false); }
  // Popular and the separate subscribed feed only exist alongside a personalized home feed.
  feedsDropdown.querySelector('.feeds-item[data-feed="/r/popular"]').hidden = !menu;
  // No separate subscribed entry when it's already what Home shows.
  feedsDropdown.querySelector('.feeds-item[data-feed="/subscribed"]').hidden = !menu || settings.homeFeed === 'subscribed';
}

function updateFeedsActive(route) {
  const current = route.type === 'saved' ? '/saved'
    : route.type === 'subscribed' ? '/subscribed'
    : route.type === 'sub' && route.sub.toLowerCase() === 'popular' ? '/r/popular'
    : null;
  feedsDropdown.querySelectorAll('.feeds-item').forEach(item => {
    const on = item.dataset.feed === current;
    item.classList.toggle('active', on);
    if (on) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────
applySettings();
state.currentCommentSort = settings.commentSort;
updateFeedsBtn();
initAutocomplete(subInput, pvSubInput, navigate, mobileSearchInput);
initCardHibernation(feed);
initKeyboard({ navigate, feed, pvContent, postView, subInput, settingsPanel, closeSettingsPanel, closeLightbox, refreshFeed: retryFeedLoad });
renderRoute(parseRoute());

// Retry home feed when app returns to foreground with no posts loaded
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  if (state.homeMode && !feed.querySelector('.post') && !state.loading) {
    loadHomeFeed(state.currentSort || 'best', state.currentTime || 'all');
  }
});
