import { state } from './state.js';
import { settings } from './settings.js';
import { escHtml, fmtNum, fmtDate, errState, buildTimeFilterHtml, SKELETON_COUNT } from './utils.js';
import { renderPost } from './render.js';
import { initMedia, initGifVideos } from './media.js';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const feed        = document.getElementById('feed');
export const sortBar     = document.getElementById('sort-bar');
const ctxInfo     = document.getElementById('ctx-info');
const sentinel    = document.getElementById('scroll-sentinel');
const subInput    = document.getElementById('subreddit-input');
const pvSubInput  = document.getElementById('pv-subreddit-input');
const mainOpen    = document.getElementById('main-open');

export function setMainOpen(href) { mainOpen.href = href || '#'; }

// ── Sort bar builders ─────────────────────────────────────────────────────────
export function buildSubSortHtml(sort='top', time='all', sub='') {
  const btns = ['hot','new','top','rising','controversial'].map(s =>
    `<button class="sort-btn${s===sort?' active':''}" data-sort="${s}">${s.charAt(0).toUpperCase()+s.slice(1)}</button>`
  ).join('');
  const isPop = sub.toLowerCase() === 'popular';
  const sidebarBtn = isPop ? '' : `<button class="sidebar-toggle" id="sidebar-toggle-btn" aria-expanded="false">sidebar</button>`;
  const wikiBtn = isPop ? '' : `<a class="sort-btn sort-btn-wiki" href="/r/${escHtml(sub)}/wiki" data-nav="/r/${escHtml(sub)}/wiki">wiki</a>`;
  return btns + (sort==='top'||sort==='controversial' ? buildTimeFilterHtml(time) : '') + sidebarBtn + wikiBtn;
}

// ── Feed utilities ────────────────────────────────────────────────────────────
export function showSkeletons() {
  state.selectedPostIdx = -1;
  sentinel.innerHTML = '';
  feed.innerHTML = Array.from({length:SKELETON_COUNT}, (_, i) => {
    if (i % 3 === 1) return `
    <div class="skeleton-post skel-compact">
      <div class="skel-compact-left">
        <div class="skel-header"><div class="skel skel-title"></div><div class="skel skel-title2"></div></div>
        <div class="skel skel-footer"></div>
      </div>
      <div class="skel skel-compact-thumb"></div>
    </div>`;
    return `
    <div class="skeleton-post">
      <div class="skel-header"><div class="skel skel-title"></div><div class="skel skel-title2"></div></div>
      <div class="skel skel-banner"></div>
      <div class="skel skel-footer"></div>
    </div>`;
  }).join('');
  sentinel.classList.remove('active', 'loading');
}

// ── Home feed ─────────────────────────────────────────────────────────────────
export function buildHomeSortHtml(sort='best', time='all') {
  const btns = ['best','hot','new','top','rising','controversial'].map(s =>
    `<button class="sort-btn${s===sort?' active':''}" data-sort="${s}">${s.charAt(0).toUpperCase()+s.slice(1)}</button>`
  ).join('');
  return btns + (sort==='top'||sort==='controversial' ? buildTimeFilterHtml(time) : '');
}

export async function loadHomeFeed(sort, time, after=null, append=false) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    let url = `/api/home?sort=${sort}`;
    if (sort === 'top' || sort === 'controversial') url += `&t=${time || 'all'}`;
    if (after) url += `&after=${encodeURIComponent(after)}`;
    const fetchOpts = settings.loid ? { headers: { 'X-Reddit-Loid': settings.loid } } : {};
    const res  = await fetch(url, fetchOpts);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error||'Error'), 'feed');
      return;
    }
    if (!append) feed.innerHTML = '';
    if (!data.posts.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No posts found</div></div>';
      return;
    }
    const startIdx = append ? feed.children.length : 0;
    const tmp = document.createElement('div');
    tmp.innerHTML = data.posts.map((p,i)=>renderPost(p,startIdx+i,true)).join('');
    initMedia(tmp);
    while (tmp.firstChild) feed.appendChild(tmp.firstChild);
    initGifVideos(feed);
    state.afterToken = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

export async function loadHome(sort='best', time='all', after=null) {
  state.homeMode    = true;
  state.profileMode = false;
  state.multiMode   = false;
  state.currentSub  = '';
  state.currentSort = sort;
  state.currentTime = time;
  state.afterToken  = null;
  state.currentAfter = after;
  document.title = 'Home — RDVWR';
  subInput.value = '';
  pvSubInput.value = '';
  setMainOpen('https://www.reddit.com/');
  sortBar.innerHTML = buildHomeSortHtml(sort, time);
  sortBar.style.display = 'flex';
  ctxInfo.classList.remove('visible');
  await loadHomeFeed(sort, time, after);
}

// ── Subreddit feed ────────────────────────────────────────────────────────────
export async function loadAbout(sub) {
  try {
    const res = await fetch(`/api/r/${encodeURIComponent(sub)}/about`);
    if (!res.ok) return;
    const d = await res.json();
    document.getElementById('ctx-icon-wrap').innerHTML = d.icon
      ? `<img class="ctx-icon" src="${escHtml(d.icon)}" alt="" onerror="this.style.display='none'">` : '';
    document.getElementById('ctx-title').textContent = d.title || `r/${sub}`;
    const activePart = d.active ? ` · <span>${fmtNum(d.active)}</span> online` : '';
    document.getElementById('ctx-stats').innerHTML = `<span>${fmtNum(d.subscribers)}</span> members${activePart}`;
    ctxInfo.classList.add('visible');
  } catch {}
}

async function fetchPosts(sub, sort, time, after) {
  let url = `/api/r/${encodeURIComponent(sub)}?sort=${sort}`;
  if (sort === 'top' || sort === 'controversial') url += `&t=${time || 'all'}`;
  if (after) url += `&after=${after}`;
  return fetch(url);
}

export async function loadSubFeed(sub, sort, time='all', after=null, append=false) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    const res  = await fetchPosts(sub, sort, time, after);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error||'Error'), 'feed');
      return;
    }
    if (!append) feed.innerHTML = '';
    if (!data.posts.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No posts found</div></div>';
      return;
    }
    const startIdx = append ? feed.children.length : 0;
    const multiSub = state.currentSub === 'popular' || state.currentSub === 'all';
    const tmp = document.createElement('div');
    tmp.innerHTML = data.posts.map((p,i)=>renderPost(p,startIdx+i,multiSub)).join('');
    initMedia(tmp);
    while (tmp.firstChild) feed.appendChild(tmp.firstChild);
    initGifVideos(feed);
    state.afterToken = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

export async function loadSubreddit(sub, sort='top', time='all', after=null) {
  state.profileMode = false;
  state.multiMode   = false;
  state.currentSub  = sub.trim();
  state.currentSort = sort;
  state.currentTime = time;
  state.afterToken  = null;
  state.currentAfter = after;
  document.title = `r/${state.currentSub} — RDVWR`;
  subInput.value = state.currentSub;
  pvSubInput.value = state.currentSub;
  setMainOpen(`https://www.reddit.com/r/${encodeURIComponent(state.currentSub)}/${sort}/`);
  sortBar.innerHTML = buildSubSortHtml(sort, time, sub);
  sortBar.style.display = 'flex';
  ctxInfo.classList.remove('visible');
  loadAbout(state.currentSub);
  await loadSubFeed(state.currentSub, state.currentSort, state.currentTime, after);
}

// ── Multi feed ────────────────────────────────────────────────────────────────
export async function loadMultiFeed(username, multiname, sort, time, after=null, append=false) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    let url = `/api/user/${encodeURIComponent(username)}/m/${encodeURIComponent(multiname)}?sort=${sort}`;
    if (sort === 'top' || sort === 'controversial') url += `&t=${time || 'all'}`;
    if (after) url += `&after=${after}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error || 'Error'), 'feed');
      return;
    }
    if (!append && data.title) {
      document.title = `${data.title} — RDVWR`;
      document.getElementById('ctx-title').textContent = data.title;
      document.getElementById('ctx-icon-wrap').innerHTML = '';
      document.getElementById('ctx-stats').innerHTML = `<span>u/${escHtml(username)}</span> multireddit`;
      ctxInfo.classList.add('visible');
    }
    if (!append) feed.innerHTML = '';
    if (!data.posts.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No posts found</div></div>';
      return;
    }
    const startIdx = append ? feed.children.length : 0;
    const tmp = document.createElement('div');
    tmp.innerHTML = data.posts.map((p, i) => renderPost(p, startIdx + i, true)).join('');
    initMedia(tmp);
    while (tmp.firstChild) feed.appendChild(tmp.firstChild);
    initGifVideos(feed);
    state.afterToken = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

export async function loadMultireddit(username, multiname, sort='hot', time='all', after=null) {
  state.profileMode   = false;
  state.multiMode     = true;
  state.multiUsername = username;
  state.multiName     = multiname;
  state.currentSort   = sort;
  state.currentTime   = time;
  state.afterToken    = null;
  state.currentAfter  = after;
  document.title = `${multiname} — RDVWR`;
  subInput.value = `user/${username}/m/${multiname}`;
  pvSubInput.value = `user/${username}/m/${multiname}`;
  setMainOpen(`https://www.reddit.com/user/${encodeURIComponent(username)}/m/${encodeURIComponent(multiname)}/${sort}/`);
  sortBar.innerHTML = buildSubSortHtml(sort, time, '');
  sortBar.style.display = 'flex';
  ctxInfo.classList.remove('visible');
  await loadMultiFeed(username, multiname, sort, time, after);
}

// ── Duplicates ────────────────────────────────────────────────────────────────
export async function loadDuplicatesPage(sub, postId, after=null, append=false) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  state.duplicatesMode   = true;
  state.duplicatesSub    = sub;
  state.duplicatesPostId = postId;
  if (!append) {
    state.duplicatesAfter = null;
    showSkeletons();
    sortBar.style.display = 'none';
    ctxInfo.classList.remove('visible');
    subInput.value = '';
    pvSubInput.value = '';
    setMainOpen(`https://www.reddit.com/r/${encodeURIComponent(sub)}/duplicates/${encodeURIComponent(postId)}`);
  } else {
    sentinel.classList.add('loading');
  }
  try {
    let url = `/api/r/${encodeURIComponent(sub)}/duplicates/${encodeURIComponent(postId)}`;
    if (after) url += `?after=${encodeURIComponent(after)}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error || 'Failed to load'), 'feed');
      return;
    }
    if (!append) {
      const p = data.post;
      if (p) {
        document.title = `Duplicates: ${p.title} — RDVWR`;
        document.getElementById('ctx-icon-wrap').innerHTML = '';
        document.getElementById('ctx-title').textContent = p.title;
        document.getElementById('ctx-stats').innerHTML =
          `<a class="ctx-sub-link" href="/r/${escHtml(p.subreddit)}" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>`;
        ctxInfo.classList.add('visible');
      } else {
        document.title = `Duplicates — RDVWR`;
      }
      const backSub  = escHtml(sub);
      const backId   = escHtml(postId);
      feed.innerHTML = `<div class="dupes-header">
        <a class="dupes-back" href="/r/${backSub}/comments/${backId}" data-nav="/r/${backSub}/comments/${backId}">← back to post</a>
        <span class="dupes-count">${data.posts.length} other post${data.posts.length !== 1 ? 's' : ''} linking to this URL</span>
      </div>`;
      if (!data.posts.length) {
        feed.insertAdjacentHTML('beforeend', '<div class="state"><div class="state-icon">∅</div><div class="state-title">No duplicates found</div></div>');
        return;
      }
    }
    const startIdx = append ? feed.querySelectorAll('.post').length : 0;
    feed.insertAdjacentHTML('beforeend', data.posts.map((p, i) => renderPost(p, startIdx + i, true)).join(''));
    initMedia(feed);
    initGifVideos(feed);
    state.duplicatesAfter = data.after;
    sentinel.classList.remove('loading');
  } catch {
    if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed');
  } finally {
    if (myGen === state.feedGen) state.loading = false;
  }
}
