import { state } from './state.js';
import { loadAccount } from './account.js';
import {
  loadSubFeed, loadMultiFeed, loadHomeFeed, loadDuplicatesPage, loadSaved, loadSubscribed,
} from './feed.js';
import { loadProfileTab } from './profile.js';
import { loadSearchResults, loadCommunityResults, loadUserResults } from './search.js';
import { loadWikiPage } from './wiki.js';
import { loadLiveThread } from './live.js';

const feed = document.getElementById('feed');

// ── Retry feed load ───────────────────────────────────────────────────────────
export function retryFeedLoad() {
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
  } else if (state.accountMode) {
    loadAccount(state.accountTab);
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

// Retry home feed when app returns to foreground with no posts loaded
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  if (state.homeMode && !feed.querySelector('.post') && !state.loading) {
    loadHomeFeed(state.currentSort || 'best', state.currentTime || 'all');
  }
});
