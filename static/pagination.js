import { state } from './state.js';
import { settings } from './settings.js';
import { navigate } from './nav.js';
import { loadSubFeed, loadMultiFeed, loadHomeFeed, loadDuplicatesPage } from './feed.js';
import { loadProfileTab } from './profile.js';
import { loadSearchResults, loadCommunityResults, loadUserResults } from './search.js';
import { loadMoreLiveUpdates } from './live.js';

const sentinel = document.getElementById('scroll-sentinel');

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
    let url = `/search?q=${encodeURIComponent(state.searchQuery)}&sort=${state.searchSort}`;
    if (state.searchTime !== 'all') url += `&t=${state.searchTime}`;
    if (state.searchSub) url += `&sub=${encodeURIComponent(state.searchSub)}`;
    if (state.searchNsfwOnly) url += '&nsfw_only=1';
    url += `&stype=${state.searchType}&after=${encodeURIComponent(after)}&page=${nextPage}`;
    return url;
  } else if (state.profileMode) {
    if (!state.profileAfter) return null;
    return `/user/${encodeURIComponent(state.profileUser)}?after=${encodeURIComponent(state.profileAfter)}&page=${nextPage}`;
  } else if (state.duplicatesMode) {
    if (!state.duplicatesAfter) return null;
    return `/r/${encodeURIComponent(state.duplicatesSub)}/duplicates/${encodeURIComponent(state.duplicatesPostId)}?after=${encodeURIComponent(state.duplicatesAfter)}&page=${nextPage}`;
  } else if (state.subsMode) {
    if (!state.afterToken) return null;
    const params = [];
    if (state.currentSort === 'top' || state.currentSort === 'controversial') params.push(`t=${state.currentTime}`);
    params.push(`after=${encodeURIComponent(state.afterToken)}`, `page=${nextPage}`);
    return `${state.subsBase}/${state.currentSort}?${params.join('&')}`;
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
