import { state } from './state.js';
import { setActiveButton } from './utils.js';
import { navigate } from './nav.js';
import { sortBar } from './feed.js';
import { loadProfileTab, buildProfileSortHtml } from './profile.js';
import { loadSearchResults, loadCommunityResults, loadUserResults, searchTypeBar } from './search.js';
import { toggleSidebar, toggleUserSidebar } from './sidebar.js';

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
  if (state.searchNsfwOnly) url += '&nsfw_only=1';
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
  if (state.subsMode) {
    navigate(`${state.subsBase}/${state.currentSort}`, { replace:true });
  } else if (state.homeMode) {
    navigate(`/home/${state.currentSort}`, { replace:true });
  } else if (state.multiMode) {
    navigate(`/user/${state.multiUsername}/m/${state.multiName}/${state.currentSort}`, { replace:true });
  } else {
    navigate(`/r/${state.currentSub}/${state.currentSort}`, { replace:true });
  }
});

// Sort bar change (time filter, scope checkbox)
sortBar.addEventListener('change', e => {
  const nsfwOnlyCheck = e.target.closest('#nsfw-only-check');
  if (nsfwOnlyCheck && state.searchMode) {
    state.searchNsfwOnly = nsfwOnlyCheck.checked;
    navigate(buildSearchUrl(), { replace:true });
    return;
  }
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
  } else if (state.subsMode) {
    state.currentTime = sel.value;
    state.afterToken = null;
    window.scrollTo({top:0, behavior:'instant'});
    navigate(`${state.subsBase}/${state.currentSort}?t=${state.currentTime}`, { replace:true });
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
