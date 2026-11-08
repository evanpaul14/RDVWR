import { state, navigationGuard } from './state.js';
import { leaveAccount, loadAccount } from './account.js';
import { _markPostVisited } from './visited-ui.js';
import { appPath } from './utils.js';
import { parseRoute } from './router.js';
import { popularIsHome, subscribedIsHome } from './home-mode.js';
import {
  loadSubreddit, loadMultireddit, loadHome, loadDuplicatesPage, loadSaved, loadSubscribed,
} from './feed.js';
import { loadProfile } from './profile.js';
import { loadSearch, searchTypeBar } from './search.js';
import { loadWikiPage } from './wiki.js';
import { loadLiveThread, cancelLivePoll } from './live.js';
import { loadPostView, closePostView } from './postview.js';
import { closeSidebar } from './sidebar.js';

const feed = document.getElementById('feed');

// Header/menu modules register here to update their chrome on every route change,
// so they don't have to be imported (and cycle back) from this file.
const routeHooks = [];
export function onRoute(fn) { routeHooks.push(fn); }

// ── Navigation ────────────────────────────────────────────────────────────────
export function navigateOrOpen(path, e) {
  if (e && (e.ctrlKey || e.metaKey || e.button === 1)) { window.open(appPath(path), '_blank'); return; }
  navigate(path);
}

export function navigate(path, { replace=false }={}) {
  const pvScrollTop = document.getElementById('pv-scroll')?.scrollTop || 0;
  history.replaceState({ ...(history.state||{}), scrollY: window.scrollY, pvScrollTop }, '', location.href);
  if (replace) history.replaceState(null,'',appPath(path));
  else         history.pushState(null,'',appPath(path));
  renderRoute(parseRoute(path));
}

let _isBootRender = true;

export async function renderRoute(route, { restoreScroll=0, restorePvScroll=0 }={}) {
  state.navigationGen++;
  const isCurrent = navigationGuard();
  const isBoot = _isBootRender;
  _isBootRender = false;
  routeHooks.forEach(fn => fn(route));
  if (route.type !== 'search') {
    searchTypeBar.style.display = 'none';
    state.searchType = 'posts';
  }
  if (route.type !== 'home') state.homeMode = false;
  if (route.type !== 'post') window.scrollTo({top: restoreScroll, behavior: 'instant'});
  if (route.type !== 'duplicates') state.duplicatesMode = false;
  if (route.type !== 'wiki') state.wikiMode = false;
  if (route.type !== 'saved') state.savedMode = false;
  if (route.type !== 'account') { state.accountMode = false; leaveAccount(); }
  if (!['home', 'subscribed', 'post'].includes(route.type)) state.subsMode = false;
  if (route.type !== 'live') { state.liveMode = false; cancelLivePoll(); }
  switch (route.type) {
    case 'home':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      if (popularIsHome()) {
        navigate('/r/popular', { replace: true });
      } else if (subscribedIsHome()) {
        await loadSubscribed(route.sort || 'best', route.time || 'all', route.after || null, '/home');
      } else {
        state.subsMode = false;
        await loadHome(route.sort || 'best', route.time || 'all', route.after || null);
      }
      break;
    case 'subscribed':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      await loadSubscribed(route.sort, route.time || 'all', route.after || null, '/subscribed');
      break;
    case 'sub': {
      closePostView();
      closeSidebar();
      state.searchMode = false;
      const subResult = await loadSubreddit(route.sub, route.sort, route.time || 'all', route.after || null);
      if (isCurrent() && subResult?.notFound) {
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
      await loadSearch(route.query, route.sort, route.time, route.sub, true, route.stype || 'posts', route.after || null, !!route.nsfwOnly);
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
    case 'account':
      closePostView();
      closeSidebar();
      state.duplicatesMode = false;
      await loadAccount(route.tab);
      break;
    case 'saved':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      state.duplicatesMode = false;
      await loadSaved();
      break;
    case 'live':
      closePostView();
      closeSidebar();
      state.searchMode = false;
      state.profileMode = false;
      await loadLiveThread(route.threadId);
      break;
  }
  if (isCurrent() && route.type !== 'post') window.scrollTo({top: restoreScroll, behavior: 'instant'});
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
  if ((route.type === 'home' || route.type === 'subscribed') && hasFeedPosts && state.subsMode && state.subsBase === (route.type === 'home' ? '/home' : '/subscribed') && (route.sort || 'best') === state.currentSort && (route.time || 'all') === state.currentTime && (route.after || null) === state.currentAfter) {
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
