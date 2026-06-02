import { state } from './state.js';
import { settings } from './settings.js';
import { escHtml, setActiveButton, errState, buildTimeFilterHtml } from './utils.js';
import { renderPost, renderCommunityCard, renderUserCard } from './render.js';
import { initMedia, initGifVideos } from './media.js';
import { showSkeletons, setMainOpen } from './feed.js';

export const searchTypeBar = document.getElementById('search-type-bar');
const feed      = document.getElementById('feed');
const sentinel  = document.getElementById('scroll-sentinel');
const sortBar   = document.getElementById('sort-bar');
const ctxInfo   = document.getElementById('ctx-info');
const subInput   = document.getElementById('subreddit-input');
const pvSubInput = document.getElementById('pv-subreddit-input');

export const SEARCH_SORT_BTN_HTML = `
  <button class="sort-btn active" data-ssort="relevance">Relevance</button>
  <button class="sort-btn" data-ssort="hot">Hot</button>
  <button class="sort-btn" data-ssort="top">Top</button>
  <button class="sort-btn" data-ssort="new">New</button>`;

export async function loadSearchResults(query, sort, time, after=null, append=false) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    let url = `/api/search?q=${encodeURIComponent(query)}&sort=${sort}&t=${time}`;
    if (state.searchSub)  url += `&sub=${encodeURIComponent(state.searchSub)}`;
    if (state.searchNsfw) url += `&nsfw=1`;
    if (after)            url += `&after=${encodeURIComponent(after)}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error||'Search failed'), 'feed');
      return;
    }
    if (!append) feed.innerHTML = '';
    if (!data.posts.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No results found</div></div>';
      return;
    }
    const startIdx = append ? feed.querySelectorAll('.post').length : 0;
    feed.insertAdjacentHTML('beforeend', data.posts.map((p,i)=>renderPost(p,startIdx+i,true)).join(''));
    initMedia(feed);
    initGifVideos(feed);
    state.searchAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

export async function loadSearch(query, sort='relevance', time='all', sub='', nsfw=true, type='posts', after=null) {
  if (settings.nsfwHide || settings.nsfwSearchHide) nsfw = false;
  if (sub) state.searchSubStored = sub;
  else if (query !== state.searchQuery) state.searchSubStored = '';

  state.searchMode  = true;
  state.profileMode = false;
  state.searchQuery = query;
  state.searchSort  = sort;
  state.searchTime  = time;
  state.searchSub   = sub;
  state.searchNsfw  = nsfw;
  state.searchAfter = null;
  state.afterToken  = null;
  state.communityAfter = null;
  state.userAfter   = null;
  state.searchType  = type;
  subInput.value = query;
  pvSubInput.value = query;
  document.title = `Search: ${query}${sub ? ` in r/${sub}` : ''} — RDVWR`;
  let _searchUrl = `https://www.reddit.com/search/?q=${encodeURIComponent(query)}&sort=${sort}`;
  if (sub) _searchUrl += `&restrict_sr=1&sr_nsfw=`;
  setMainOpen(_searchUrl);

  document.getElementById('ctx-icon-wrap').innerHTML = '';
  document.getElementById('ctx-title').textContent = sub ? `r/${sub}: "${query}"` : `Search: "${query}"`;
  document.getElementById('ctx-stats').innerHTML = `<span>${sort}</span>${time !== 'all' ? ` · <span>${time}</span>` : ''}`;
  ctxInfo.classList.add('visible');

  const scopeCheckHtml = state.searchSubStored
    ? `<label class="scope-check-label"><input type="checkbox" class="scope-check-input" id="scope-check"${sub ? ' checked' : ''}><span>r/${escHtml(state.searchSubStored)}</span></label>`
    : '';
  sortBar.innerHTML = SEARCH_SORT_BTN_HTML + (sort === 'top' ? buildTimeFilterHtml(time) : '') + scopeCheckHtml;
  sortBar.style.display = 'flex';
  setActiveButton(sortBar, 'ssort', sort);

  const isScopedSearch = !!sub || query.includes('flair:');
  if (isScopedSearch) {
    searchTypeBar.style.display = 'none';
    state.searchType = 'posts';
    type = 'posts';
  } else {
    searchTypeBar.style.display = 'flex';
    setActiveButton(searchTypeBar, 'stype', type);
  }
  sortBar.style.display = type === 'posts' ? 'flex' : 'none';

  if (type === 'communities') { await loadCommunityResults(query, after); }
  else if (type === 'users')  { await loadUserResults(query, after); }
  else                        { await loadSearchResults(query, sort, time, after); }
}

export async function loadCommunityResults(query, after=null, append=false) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    let url = `/api/search/communities?q=${encodeURIComponent(query)}`;
    if (after) url += `&after=${encodeURIComponent(after)}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!append) feed.innerHTML = '';
    if (!data.communities?.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No communities found</div></div>';
      return;
    }
    const startIdx = append ? feed.children.length : 0;
    feed.insertAdjacentHTML('beforeend', data.communities.map((c,i)=>renderCommunityCard(c,startIdx+i)).join(''));
    state.communityAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen===state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen===state.feedGen) state.loading = false; }
}

export async function loadUserResults(query, after=null, append=false) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    let url = `/api/search/users?q=${encodeURIComponent(query)}`;
    if (after) url += `&after=${encodeURIComponent(after)}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!append) feed.innerHTML = '';
    if (!data.users?.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No users found</div></div>';
      return;
    }
    const startIdx = append ? feed.children.length : 0;
    feed.insertAdjacentHTML('beforeend', data.users.map((u,i)=>renderUserCard(u,startIdx+i)).join(''));
    state.userAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen===state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen===state.feedGen) state.loading = false; }
}
