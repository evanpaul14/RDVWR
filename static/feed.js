import { state } from './state.js';
import { escHtml, fmtNum, fmtDate, timeAgo, setActiveButton, renderFlair, renderAwards, SKELETON_COUNT } from './utils.js';
import { initVideos, initRedgifs, initImgurAlbums, mediaHtmlFull } from './media.js';
import { renderPost, renderCommentTree, renderUserCommentCard, renderCommunityCard, renderUserCard, renderMd, translatePost, renderLiveUpdate, renderCrosspostFull } from './render.js';

// ── Download helper ───────────────────────────────────────────────────────────
const _PV_DL_HOSTS = new Set(['v.redd.it','i.redd.it','preview.redd.it','external-preview.redd.it','i.imgur.com']);
function _pvDlOk(url) {
  if (!url) return false;
  try { return _PV_DL_HOSTS.has(new URL(url).hostname); } catch { return false; }
}
function _pvDlExt(url) {
  const ext = url.split('?')[0].split('.').pop().toLowerCase();
  return ['jpg','jpeg','png','gif','webp'].includes(ext) ? ext : 'jpg';
}
const _DL_SVG = `<svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M8 2v8M5 7l3 3 3-3M3 13h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
function buildDownloadBtn(p) {
  // Redgifs: placeholder replaced by initRedgifs once video URL is resolved
  if (p.redgifs_id) {
    return `<span class="share-btn pv-dl-placeholder" data-rg-dl="${escHtml(p.redgifs_id)}" title="Download (loading…)">${_DL_SVG} download</span>`;
  }
  // Gallery: downloads current image; gallery nav updates the href
  if (p.gallery?.length) {
    const first = p.gallery[0].url;
    if (!_pvDlOk(first)) return '';
    const base = first.split('?')[0].split('/').pop() || 'image';
    const dot = base.lastIndexOf('.');
    const fname = dot > 0 ? `${base.slice(0, dot)}-1${base.slice(dot)}` : `${base}-1`;
    return `<a class="share-btn pv-dl-gallery" href="${escHtml(`/api/download?url=${encodeURIComponent(first)}&filename=${encodeURIComponent(fname)}`)}" download="${escHtml(fname)}" title="Download current image">${_DL_SVG} download</a>`;
  }
  // Imgur album: placeholder replaced by initImgurAlbums once images are loaded
  if (p.imgur_album_id) {
    return `<span class="share-btn pv-dl-placeholder" data-imgur-dl="${escHtml(p.imgur_album_id)}" title="Download (loading…)">${_DL_SVG} download</span>`;
  }
  let url = '', filename = '';
  if (p.is_video && p.video_url) {
    if (p.hls_url) {
      const href = `/api/download/reddit-video?hls=${encodeURIComponent(p.hls_url)}&filename=${encodeURIComponent(p.id + '.mp4')}`;
      return `<a class="share-btn" href="${escHtml(href)}" download="${escHtml(p.id + '.mp4')}" title="Download video">${_DL_SVG} download</a>`;
    }
    url = p.video_url; filename = `${p.id}.mp4`;
  } else if (p.gif_url) {
    url = p.gif_url; filename = `${p.id}.${p.gif_is_video ? 'mp4' : 'gif'}`;
  } else if (!p.youtube_id && !p.tiktok_id && !p.streamable_id && !p.embed_url && !p.is_self && p.preview_img) {
    url = p.preview_img; filename = `${p.id}.${_pvDlExt(p.preview_img)}`;
  }
  if (!url || !_pvDlOk(url)) return '';
  const href = `/api/download?url=${encodeURIComponent(url)}&filename=${encodeURIComponent(filename)}`;
  return `<a class="share-btn" href="${escHtml(href)}" download="${escHtml(filename)}" title="Download media">${_DL_SVG} download</a>`;
}

// ── DOM refs ──────────────────────────────────────────────────────────────────
const feed        = document.getElementById('feed');
const sortBar     = document.getElementById('sort-bar');
const ctxInfo     = document.getElementById('ctx-info');
const sentinel    = document.getElementById('scroll-sentinel');
const subInput    = document.getElementById('subreddit-input');
const pvSubInput  = document.getElementById('pv-subreddit-input');
const postView    = document.getElementById('post-view');
const pvContent   = document.getElementById('pv-content');
const pvScroll    = document.getElementById('pv-scroll');
const pvOpen      = document.getElementById('pv-open');
const mainOpen    = document.getElementById('main-open');
const pvBreadcrumb = document.getElementById('pv-breadcrumb');

function setMainOpen(href) { mainOpen.href = href || '#'; }
export const searchTypeBar = document.getElementById('search-type-bar');

// ── Sidebar state (private) ────────────────────────────────────────────────────
let sidebarOpen   = false;
let sidebarSub    = '';
let _sidebarCache = new Map();
const SIDEBAR_CACHE_TTL = 5 * 60 * 1000;
const sidebarPanel = document.getElementById('sidebar-panel');
const sidebarInner = document.getElementById('sidebar-inner');

// ── Post view private state ────────────────────────────────────────────────────
let _pvPrevFocus = null;

// ── Comment sort constants ─────────────────────────────────────────────────────
const COMMENT_SORTS = [
  {value:'confidence',    label:'Best'},
  {value:'top',           label:'Top'},
  {value:'new',           label:'New'},
  {value:'controversial', label:'Controversial'},
  {value:'old',           label:'Old'},
  {value:'qa',            label:'Q&A'},
];

// ── Sort bar builders ─────────────────────────────────────────────────────────
export function buildSubSortHtml(sort='top', time='all', sub='') {
  const btns = ['hot','new','top','rising','controversial'].map(s =>
    `<button class="sort-btn${s===sort?' active':''}" data-sort="${s}">${s.charAt(0).toUpperCase()+s.slice(1)}</button>`
  ).join('');
  const isPop = sub.toLowerCase() === 'popular';
  const isHome = sub === '__home__';
  const sidebarBtn = (isPop || isHome) ? '' : `<button class="sidebar-toggle" id="sidebar-toggle-btn" aria-expanded="false">sidebar</button>`;
  const wikiBtn = (isPop || isHome) ? '' : `<a class="sort-btn sort-btn-wiki" href="javascript:;" data-nav="/r/${escHtml(sub)}/wiki">wiki</a>`;
  return btns + (sort==='top'||sort==='controversial' ? buildTimeFilterHtml(time) : '') + sidebarBtn + wikiBtn;
}

export function buildProfileSortHtml(tab='overview', sort='new', time='all') {
  const tabBtns = [
    `<button class="sort-btn${tab==='overview'?' active':''}" data-ptab="overview">Overview</button>`,
    `<button class="sort-btn${tab==='posts'?' active':''}" data-ptab="posts">Posts</button>`,
    `<button class="sort-btn${tab==='comments'?' active':''}" data-ptab="comments">Comments</button>`,
  ].join('');
  const sorts = tab==='comments' ? ['new','top'] : ['hot','new','top'];
  const sortBtns = sorts.map(s =>
    `<button class="sort-btn${s===sort?' active':''}" data-psort="${s}">${s.charAt(0).toUpperCase()+s.slice(1)}</button>`
  ).join('');
  return tabBtns + `<div style="display:flex;align-items:center;border-left:1px solid var(--b);margin-left:4px;padding-left:8px;gap:2px">` + sortBtns + `</div>` + (sort==='top' ? buildTimeFilterHtml(time) : '');
}

export const SEARCH_SORT_BTN_HTML = `
  <button class="sort-btn active" data-ssort="relevance">Relevance</button>
  <button class="sort-btn" data-ssort="hot">Hot</button>
  <button class="sort-btn" data-ssort="top">Top</button>
  <button class="sort-btn" data-ssort="new">New</button>`;

function buildTimeFilterHtml(selected) {
  return `<div class="time-filter-wrap"><select class="time-filter" id="time-filter">
    <option value="all"${selected==='all'?' selected':''}>All time</option>
    <option value="year"${selected==='year'?' selected':''}>Past year</option>
    <option value="month"${selected==='month'?' selected':''}>Past month</option>
    <option value="week"${selected==='week'?' selected':''}>Past week</option>
    <option value="day"${selected==='day'?' selected':''}>Today</option>
  </select></div>`;
}

// ── Feed utilities ────────────────────────────────────────────────────────────
function showSkeletons() {
  state.selectedPostIdx = -1;
  feed.innerHTML = Array.from({length:SKELETON_COUNT}, ()=>`
    <div class="skeleton-post">
      <div class="skel-header"><div class="skel skel-title"></div><div class="skel skel-title2"></div></div>
      <div class="skel skel-banner"></div>
      <div class="skel skel-footer"></div>
    </div>`).join('');
  sentinel.classList.remove('active', 'loading');
}

export function errState(msg, retryTarget) {
  return `<div class="state"><div class="state-icon">⚠</div><div class="state-title">${msg}</div><button class="state-retry-btn" data-retry="${retryTarget}">Try again</button></div>`;
}

export function retryFeedLoad() {
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
    loadHomeSubFeed(state.currentSort, state.currentTime);
  } else {
    loadSubFeed(state.currentSub, state.currentSort, state.currentTime);
  }
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
    initVideos(tmp); initRedgifs(tmp); initImgurAlbums(tmp);
    while (tmp.firstChild) feed.appendChild(tmp.firstChild);
    state.afterToken = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

export async function loadSubreddit(sub, sort='top', time='all') {
  state.profileMode = false;
  state.multiMode   = false;
  state.currentSub  = sub.trim();
  state.currentSort = sort;
  state.currentTime = time;
  state.afterToken  = null;
  document.title = `r/${state.currentSub} — RDVWR`;
  subInput.value = state.currentSub;
  pvSubInput.value = state.currentSub;
  setMainOpen(`https://www.reddit.com/r/${encodeURIComponent(state.currentSub)}/${sort}/`);
  sortBar.innerHTML = buildSubSortHtml(sort, time, sub);
  sortBar.style.display = 'flex';
  ctxInfo.classList.remove('visible');
  loadAbout(state.currentSub);
  await loadSubFeed(state.currentSub, state.currentSort, state.currentTime);
}

// ── Home feed ─────────────────────────────────────────────────────────────────
export async function loadHomeSubFeed(sort, time, after=null, append=false) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  const loid = localStorage.getItem('redditLoid') || '';
  const pc   = localStorage.getItem('redditPc')   || '';
  try {
    let url = `/api/home?sort=${sort}`;
    if (sort === 'top' || sort === 'controversial') url += `&t=${time || 'all'}`;
    if (after) url += `&after=${after}`;
    if (loid) url += `&loid=${encodeURIComponent(loid)}`;
    if (pc)   url += `&pc=${encodeURIComponent(pc)}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error || 'Error'), 'feed');
      return;
    }
    if (!append) feed.innerHTML = '';
    if (!data.posts.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No posts found</div></div>';
      return;
    }
    const startIdx = append ? feed.children.length : 0;
    const tmp = document.createElement('div');
    tmp.innerHTML = data.posts.map((p, i) => renderPost(p, startIdx + i, true)).join('');
    initVideos(tmp); initRedgifs(tmp); initImgurAlbums(tmp);
    while (tmp.firstChild) feed.appendChild(tmp.firstChild);
    state.afterToken = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

export async function loadHomeFeed(sort='hot', time='all') {
  state.homeMode    = true;
  state.profileMode = false;
  state.multiMode   = false;
  state.currentSub  = '';
  state.currentSort = sort;
  state.currentTime = time;
  state.afterToken  = null;
  document.title = 'Home — RDVWR';
  subInput.value = '';
  pvSubInput.value = '';
  setMainOpen('https://www.reddit.com/');
  sortBar.innerHTML = buildSubSortHtml(sort, time, '__home__');
  sortBar.style.display = 'flex';
  ctxInfo.classList.remove('visible');
  await loadHomeSubFeed(sort, time);
}

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
    initVideos(tmp); initRedgifs(tmp); initImgurAlbums(tmp);
    while (tmp.firstChild) feed.appendChild(tmp.firstChild);
    state.afterToken = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

export async function loadMultireddit(username, multiname, sort='hot', time='all') {
  state.profileMode   = false;
  state.multiMode     = true;
  state.multiUsername = username;
  state.multiName     = multiname;
  state.currentSort   = sort;
  state.currentTime   = time;
  state.afterToken    = null;
  document.title = `${multiname} — RDVWR`;
  subInput.value = `user/${username}/m/${multiname}`;
  pvSubInput.value = `user/${username}/m/${multiname}`;
  setMainOpen(`https://www.reddit.com/user/${encodeURIComponent(username)}/m/${encodeURIComponent(multiname)}/${sort}/`);
  sortBar.innerHTML = buildSubSortHtml(sort, time, '');
  sortBar.style.display = 'flex';
  ctxInfo.classList.remove('visible');
  await loadMultiFeed(username, multiname, sort, time);
}

// ── Profile feed ──────────────────────────────────────────────────────────────
export async function loadProfileTab(username, tab, sort='new', time='all', after=null, append=false) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  if (!append) { showSkeletons(); state.profileAfter = null; }
  else sentinel.classList.add('loading');
  try {
    let url;
    if (tab === 'overview') {
      url = `/api/user/${encodeURIComponent(username)}/overview?sort=${sort}`;
      if (sort === 'top') url += `&t=${time || 'all'}`;
    } else {
      const endpoint = tab === 'posts' ? 'posts' : 'comments';
      url = `/api/user/${encodeURIComponent(username)}/${endpoint}?sort=${sort}`;
      if (sort === 'top') url += `&t=${time || 'all'}`;
    }
    if (after) url += `&after=${after}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error||'Error'), 'feed');
      return;
    }
    if (!append) feed.innerHTML = '';

    if (tab === 'overview') {
      const items = data.items;
      if (!items?.length && !append) {
        feed.innerHTML = `<div class="state"><div class="state-icon">∅</div><div class="state-title">Nothing here</div></div>`;
        return;
      }
      const startIdx = append ? feed.children.length : 0;
      const tmp = document.createElement('div');
      tmp.innerHTML = items.map((item, i) =>
        item.type === 'post'
          ? renderPost(item.data, startIdx + i, true)
          : renderUserCommentCard(item.data, startIdx + i)
      ).join('');
      initVideos(tmp); initRedgifs(tmp); initImgurAlbums(tmp);
      while (tmp.firstChild) feed.appendChild(tmp.firstChild);
    } else {
      const items = tab === 'posts' ? data.posts : data.comments;
      if (!items?.length && !append) {
        feed.innerHTML = `<div class="state"><div class="state-icon">∅</div><div class="state-title">Nothing here</div></div>`;
        return;
      }
      const startIdx = append ? feed.children.length : 0;
      if (tab === 'posts') {
        const tmp = document.createElement('div');
        tmp.innerHTML = items.map((p,i)=>renderPost(p,startIdx+i,true)).join('');
        initVideos(tmp); initRedgifs(tmp); initImgurAlbums(tmp);
        while (tmp.firstChild) feed.appendChild(tmp.firstChild);
      } else {
        feed.insertAdjacentHTML('beforeend', items.map((c,i)=>renderUserCommentCard(c,startIdx+i)).join(''));
      }
    }
    state.profileAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

export async function loadProfile(username) {
  state.profileMode = true; state.profileUser = username; state.profileTab = 'overview'; state.profileSort = 'new'; state.profileTime = 'all'; state.profileAfter = null;
  sortBar.style.display = 'none';
  ctxInfo.classList.remove('visible');
  subInput.value = '';
  pvSubInput.value = '';
  document.title = `u/${username} — RDVWR`;
  setMainOpen(`https://www.reddit.com/user/${encodeURIComponent(username)}/`);

  const aboutFetch = fetch(`/api/user/${encodeURIComponent(username)}/about`);
  sortBar.innerHTML = buildProfileSortHtml(state.profileTab, state.profileSort, state.profileTime);
  sortBar.style.display = 'flex';

  const [, aboutRes] = await Promise.all([
    loadProfileTab(username, 'overview', state.profileSort, state.profileTime),
    aboutFetch
  ]);
  try {
    if (aboutRes.ok) {
      const d = await aboutRes.json();
      document.getElementById('ctx-icon-wrap').innerHTML = d.icon
        ? `<img class="ctx-icon" src="${escHtml(d.icon)}" alt="" onerror="this.style.display='none'">` : '';
      document.getElementById('ctx-title').textContent = `u/${d.name}`;
      document.getElementById('ctx-stats').innerHTML =
        `<span>${fmtNum(d.karma_post)}</span> post karma · <span>${fmtNum(d.karma_comment)}</span> comment karma · joined ${fmtDate(d.created_utc)}`;
      ctxInfo.classList.add('visible');
    }
  } catch {}
}

// ── Search feed ───────────────────────────────────────────────────────────────
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
    initVideos(feed);
    initRedgifs(feed);
    initImgurAlbums(feed);
    state.searchAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

export async function loadSearch(query, sort='relevance', time='all', sub='', nsfw=true, type='posts') {
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

  const nsfwToggleHtml = `<button class="nsfw-toggle${nsfw?' active':''}" id="nsfw-toggle">18+</button>`;
  const scopeCheckHtml = state.searchSubStored
    ? `<label class="scope-check-label"><input type="checkbox" class="scope-check-input" id="scope-check"${sub ? ' checked' : ''}><span>r/${escHtml(state.searchSubStored)}</span></label>`
    : '';
  sortBar.innerHTML = SEARCH_SORT_BTN_HTML + (sort === 'top' ? buildTimeFilterHtml(time) : '') + nsfwToggleHtml + scopeCheckHtml;
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

  if (type === 'communities') { await loadCommunityResults(query); }
  else if (type === 'users')  { await loadUserResults(query); }
  else                        { await loadSearchResults(query, sort, time); }
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
          `<a class="ctx-sub-link" href="javascript:;" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>`;
        ctxInfo.classList.add('visible');
      } else {
        document.title = `Duplicates — RDVWR`;
      }
      const backSub  = escHtml(sub);
      const backId   = escHtml(postId);
      feed.innerHTML = `<div class="dupes-header">
        <a class="dupes-back" href="javascript:;" data-nav="/r/${backSub}/comments/${backId}">← back to post</a>
        <span class="dupes-count">${data.posts.length} other post${data.posts.length !== 1 ? 's' : ''} linking to this URL</span>
      </div>`;
      if (!data.posts.length) {
        feed.insertAdjacentHTML('beforeend', '<div class="state"><div class="state-icon">∅</div><div class="state-title">No duplicates found</div></div>');
        return;
      }
    }
    const startIdx = append ? feed.querySelectorAll('.post').length : 0;
    feed.insertAdjacentHTML('beforeend', data.posts.map((p, i) => renderPost(p, startIdx + i, true)).join(''));
    initVideos(feed);
    initRedgifs(feed);
    initImgurAlbums(feed);
    state.duplicatesAfter = data.after;
    sentinel.classList.remove('loading');
  } catch {
    if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed');
  } finally {
    if (myGen === state.feedGen) state.loading = false;
  }
}

// ── Wiki ──────────────────────────────────────────────────────────────────────
export async function loadWikiPage(sub, page) {
  state._wikiSub = sub; state._wikiPage = page;
  state.wikiMode = true; state.afterToken = null;
  setMainOpen(`https://www.reddit.com/r/${encodeURIComponent(sub)}/wiki/${encodeURIComponent(page)}`);
  feed.innerHTML = '<div class="state"><div class="state-icon">⌗</div><div class="state-title">Loading…</div></div>';
  sortBar.innerHTML = `<a class="sort-btn" href="javascript:;" data-nav="/r/${escHtml(sub)}">← r/${escHtml(sub)}</a>`;
  document.title = `${page} — ${sub} wiki — RDVWR`;
  try {
    const res = await fetch(`/api/r/${encodeURIComponent(sub)}/wiki/${encodeURIComponent(page)}`);
    const data = await res.json();
    if (!res.ok) { feed.innerHTML = errState(escHtml(data.error || 'Failed to load wiki'), 'wiki'); return; }
    const revHtml = data.revision_date
      ? `<div class="wiki-meta">Last revised ${fmtDate(data.revision_date)}</div>`
      : '';
    feed.innerHTML = `
      <div class="wiki-page">
        <div class="wiki-header">
          <span class="wiki-sub"><a href="javascript:;" data-nav="/r/${escHtml(sub)}">r/${escHtml(sub)}</a></span>
          <span class="wiki-sep">/</span>
          <span class="wiki-title">wiki/${escHtml(page)}</span>
        </div>
        ${revHtml}
        <div class="wiki-body md">${DOMPurify.sanitize(data.content_html || '', { ADD_ATTR: ['id', 'name'], FORBID_ATTR: ['style'] })}</div>
      </div>`;
    feed.addEventListener('click', e => {
      const a = e.target.closest('a[href^="#"]');
      if (!a) return;
      const id = a.getAttribute('href').slice(1);
      const target = feed.querySelector(`#${CSS.escape(id)}`);
      if (target) { e.preventDefault(); target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    }, { once: true });
  } catch {
    feed.innerHTML = errState('Network error', 'wiki');
  }
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
export function closeSidebar() {
  sidebarOpen = false;
  sidebarPanel.classList.remove('open');
  const btn = document.getElementById('sidebar-toggle-btn');
  if (btn) { btn.classList.remove('active'); btn.setAttribute('aria-expanded', 'false'); }
}

export async function toggleSidebar(sub) {
  if (sidebarOpen && sidebarSub === sub) { closeSidebar(); return; }
  sidebarSub = sub;
  sidebarOpen = true;
  sidebarPanel.classList.add('open');
  const btn = document.getElementById('sidebar-toggle-btn');
  if (btn) { btn.classList.add('active'); btn.setAttribute('aria-expanded', 'true'); }

  const cached = _sidebarCache.get(sub);
  if (cached && Date.now() - cached.ts < SIDEBAR_CACHE_TTL) {
    sidebarInner.innerHTML = cached.html;
    return;
  }

  sidebarInner.innerHTML = '<div style="padding:10px 0;font-family:var(--mono);font-size:11px;color:var(--tx3)">Loading…</div>';

  try {
    const [aboutRes, rulesRes] = await Promise.all([
      fetch(`/api/r/${encodeURIComponent(sub)}/about`),
      fetch(`/api/r/${encodeURIComponent(sub)}/rules`),
    ]);
    const about = aboutRes.ok ? await aboutRes.json() : {};
    const rulesData = rulesRes.ok ? await rulesRes.json() : {rules:[]};

    let html = '';
    if (about.description) {
      html += `<div class="sidebar-section">
        <div class="sidebar-section-title">About</div>
        <div class="sidebar-desc md">${renderMd(about.sidebar || about.description)}</div>
      </div>`;
    }
    if (rulesData.rules?.length) {
      const rulesHtml = rulesData.rules.map((r, i) =>
        `<li class="sidebar-rule"><span class="sidebar-rule-num">${i+1}.</span>${escHtml(r.short_name)}</li>`
      ).join('');
      html += `<div class="sidebar-section">
        <div class="sidebar-section-title">Rules</div>
        <ul class="sidebar-rules">${rulesHtml}</ul>
      </div>`;
    }
    if (!html) html = '<div style="font-family:var(--mono);font-size:11px;color:var(--tx3)">No sidebar content.</div>';
    _sidebarCache.set(sub, { html, ts: Date.now() });
    sidebarInner.innerHTML = html;
  } catch {
    sidebarInner.innerHTML = '<div style="font-family:var(--mono);font-size:11px;color:var(--tx3)">Failed to load sidebar.</div>';
  }
}

// ── Community / user results ──────────────────────────────────────────────────
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

// ── Post view ─────────────────────────────────────────────────────────────────
export function openPostView() {
  _pvPrevFocus = document.activeElement;
  postView.classList.add('open');
  document.body.style.overflow = 'hidden';
  const focusEl = document.getElementById('pv-home');
  if (focusEl) focusEl.focus();
}

export function closePostView() {
  postView.classList.remove('open');
  document.body.style.overflow = '';
  if (_pvPrevFocus) { _pvPrevFocus.focus(); _pvPrevFocus = null; }
}

function findComment(comments, id) {
  for (const c of comments) {
    if (c.id === id) return c;
    if (c.replies?.length) {
      const found = findComment(c.replies, id);
      if (found) return found;
    }
  }
  return null;
}

function buildCommentSortBar(active) {
  return `<div class="comment-sort-bar">${COMMENT_SORTS.map(s =>
    `<button class="sort-btn${s.value===active?' active':''}" data-csort="${s.value}">${s.label}</button>`
  ).join('')}</div>`;
}

function buildCommentsHtml(data, commentId) {
  const p = data.post;
  const threadBanner = commentId ? `<div class="thread-banner"><a href="javascript:;" data-back="true">← View full thread</a></div>` : '';
  let rootComments = data.comments;
  if (commentId) {
    const target = findComment(data.comments, commentId);
    if (target) rootComments = [target];
  }
  if (!rootComments.length) return '<div class="state" style="padding:40px 0"><div class="state-icon">∅</div><div class="state-title">No comments yet</div></div>';
  return `<div class="pv-comments">${threadBanner}${renderCommentTree(rootComments, 0, p.subreddit, p.id, p.author)}</div>`;
}

export async function changeCommentSort(sort) {
  state.currentCommentSort = sort;
  setActiveButton(pvContent, 'csort', sort);
  const area = pvContent.querySelector('.pv-comments-area');
  if (!area) return;
  area.innerHTML = '<div class="state" style="padding:30px 0"><div class="state-icon">⌗</div><div class="state-title">Loading…</div></div>';
  try {
    const apiUrl = `/api/r/${encodeURIComponent(state._pvSub)}/comments/${encodeURIComponent(state._pvPostId)}?sort=${sort}${state._pvCommentId ? `&comment=${encodeURIComponent(state._pvCommentId)}` : ''}`;
    const res  = await fetch(apiUrl);
    if (!res.ok) { area.innerHTML = errState('Failed to load comments', 'comments'); return; }
    const data = await res.json();
    state._pvData = data;
    area.innerHTML = buildCommentsHtml(data, state._pvCommentId);
  } catch {
    area.innerHTML = errState('Network error', 'comments');
  }
}

export async function loadPostView(sub, postId, commentId='', restorePvScroll=0) {
  state._pvSub = sub; state._pvPostId = postId; state._pvCommentId = commentId;
  state.currentCommentSort = 'confidence';
  pvContent.innerHTML = '<div class="state"><div class="state-icon">⌗</div><div class="state-title">Loading…</div></div>';
  pvScroll.scrollTop = 0;
  openPostView();

  pvBreadcrumb.innerHTML = `<a href="javascript:;" data-nav="/r/${escHtml(sub)}">r/${escHtml(sub)}</a>`;
  pvOpen.href = '#';

  try {
    const apiUrl = `/api/r/${encodeURIComponent(sub)}/comments/${encodeURIComponent(postId)}?sort=confidence` + (commentId ? `&comment=${encodeURIComponent(commentId)}` : '');
    const res  = await fetch(apiUrl);
    const data = await res.json();
    if (!res.ok) { pvContent.innerHTML = errState(escHtml(data.error||'Failed to load'), 'post'); return; }
    state._pvData = data;

    const p = data.post;
    pvOpen.href = p.permalink;
    pvBreadcrumb.innerHTML = `<a href="javascript:;" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>`;
    document.title = p.title + ' — RDVWR';

    const titleClass = 'pv-title'+(p.is_self?' is-italic':'');
    const pvBadges = [
      p.is_stickied ? '<span class="badge badge-sticky">📌 pinned</span>' : '',
      p.over_18     ? '<span class="nsfw-tag">nsfw</span>' : '',
      p.is_spoiler  ? '<span class="badge badge-spoiler">spoiler</span>' : '',
      p.locked      ? '<span class="badge badge-locked">locked</span>' : '',
      p.is_oc       ? '<span class="badge badge-oc">oc</span>' : '',
      renderFlair(p),
    ].filter(Boolean).join('');
    const pvEditedHtml = p.edited_utc ? `<span class="edited-mark" title="edited ${fmtDate(p.edited_utc)}">*edited ${timeAgo(p.edited_utc)}</span>` : '';
    const bodyHtml = p.selftext?.trim() ? `<div class="pv-body md">${renderMd(p.selftext)}</div>` : '';
    const crosspostHtml = p.crosspost_from ? renderCrosspostFull(p.crosspost_from) : '';

    pvContent.innerHTML = `
      <a class="pv-sub-link" href="javascript:;" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>
      ${pvBadges ? `<div class="post-meta-top" style="margin-bottom:10px">${pvBadges}</div>` : ''}
      <h1 class="${titleClass}">${escHtml(p.title)}</h1>
      <div class="pv-meta">
        <span class="up">▲ ${fmtNum(p.score)}</span>
        <span>${p.upvote_ratio}% upvoted</span>
        <button class="meta-item link" data-user="${escHtml(p.author)}">u/${escHtml(p.author)}</button>
        <span>${timeAgo(p.created_utc)}${pvEditedHtml ? ' '+pvEditedHtml : ''}</span>
        <span>${fmtNum(p.num_comments)} comments</span>
        ${!p.is_self && p.domain && !p.domain.startsWith('self.') && !p.crosspost_from ? `<a class="meta-item link" href="${escHtml(p.url)}" target="_blank" rel="noopener">${escHtml(p.domain)} ↗</a>` : ''}
        ${!p.is_self && !p.crosspost_from ? `<a class="meta-item link" href="javascript:;" data-nav="/r/${escHtml(p.subreddit)}/duplicates/${escHtml(p.id)}">duplicates</a>` : ''}
        <button class="share-btn" data-share="/r/${escHtml(p.subreddit)}/comments/${escHtml(p.id)}" title="Copy link">
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><circle cx="12" cy="3" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="12" cy="13" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="8" r="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M10.5 3.87 5.5 7.13M5.5 8.87l5 3.26" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
          share
        </button>
        ${buildDownloadBtn(p)}
        ${renderAwards(p.awards)}
      </div>
      ${crosspostHtml}
      ${p.crosspost_from ? '' : mediaHtmlFull(p)}
      ${bodyHtml}
      <div class="pv-divider">
        <div class="pv-divider-line"></div>
      </div>
      ${buildCommentSortBar('confidence')}
      <div class="pv-comments-area">
        ${buildCommentsHtml(data, commentId)}
      </div>`;

    initVideos(pvContent);
    initRedgifs(pvContent);
    initImgurAlbums(pvContent);
    if (restorePvScroll) pvScroll.scrollTop = restorePvScroll;
    translatePost(p, pvContent).catch(() => {});
  } catch {
    pvContent.innerHTML = errState('Network error', 'post');
  }
}

// ── Live threads ──────────────────────────────────────────────────────────────

let _liveTimer = null;

export function cancelLivePoll() {
  if (_liveTimer) { clearTimeout(_liveTimer); _liveTimer = null; }
}

export async function loadLiveThread(threadId) {
  cancelLivePoll();
  state.liveMode      = true;
  state.liveThreadId  = threadId;
  state.liveState     = 'complete';
  state.liveAfter     = null;
  state._liveNewestId = '';
  state.profileMode   = false;
  state.multiMode     = false;
  state.searchMode    = false;
  state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  subInput.value   = '';
  pvSubInput.value = '';
  sortBar.style.display = 'none';
  ctxInfo.classList.remove('visible');
  setMainOpen(`https://www.reddit.com/live/${encodeURIComponent(threadId)}`);
  feed.innerHTML = '<div class="state"><div class="state-icon">⌗</div><div class="state-title">Loading…</div></div>';
  try {
    const res  = await fetch(`/api/live/${encodeURIComponent(threadId)}`);
    const data = await res.json();
    if (myGen !== state.feedGen) return;
    if (!res.ok) { feed.innerHTML = errState(escHtml(data.error || 'Failed to load live thread'), 'feed'); return; }

    state.liveState = data.state;
    state.liveAfter = data.after;
    if (data.updates?.length) state._liveNewestId = data.updates[0].id;

    document.title = `${data.title} — LIVE — RDVWR`;
    const isLive  = data.state === 'live';
    const badge   = isLive
      ? `<span class="live-badge live-badge-active"><span class="live-dot"></span>LIVE</span>`
      : `<span class="live-badge live-badge-closed">CLOSED</span>`;
    const viewers  = isLive && data.viewer_count > 0 ? `<span class="live-viewers">${fmtNum(data.viewer_count)} watching</span>` : '';
    const descHtml = data.description?.trim() ? `<div class="live-desc md">${renderMd(data.description)}</div>` : '';
    const updatesHtml = data.updates.length
      ? data.updates.map(u => renderLiveUpdate(u)).join('')
      : '<div class="live-empty">No updates yet</div>';
    feed.innerHTML = `<div class="live-page">
      <div class="live-header">
        <div class="live-header-top">${badge}<h1 class="live-title">${escHtml(data.title)}</h1></div>
        ${descHtml}${viewers}
      </div>
      <div class="live-updates">${updatesHtml}</div>
    </div>`;

    if (isLive && state._liveNewestId) {
      _liveTimer = setTimeout(() => _pollLiveUpdates(threadId, myGen), 30000);
    }
  } catch { if (myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === state.feedGen) state.loading = false; }
}

async function _pollLiveUpdates(threadId, myGen) {
  _liveTimer = null;
  if (myGen !== state.feedGen || !state.liveMode || state.liveThreadId !== threadId) return;
  const newestId = state._liveNewestId;
  if (newestId) {
    try {
      const res  = await fetch(`/api/live/${encodeURIComponent(threadId)}/updates?before=${encodeURIComponent('LiveUpdate_' + newestId)}`);
      if (myGen !== state.feedGen || !state.liveMode || state.liveThreadId !== threadId) return;
      if (res.ok) {
        const data = await res.json();
        if (data.updates?.length) {
          state._liveNewestId = data.updates[0].id;
          const container = feed.querySelector('.live-updates');
          if (container) {
            const tmp = document.createElement('div');
            tmp.innerHTML = data.updates.map(u => renderLiveUpdate(u, true)).join('');
            while (tmp.lastChild) container.prepend(tmp.lastChild);
          }
        }
      }
    } catch {}
  }
  if (myGen === state.feedGen && state.liveMode && state.liveState === 'live' && state.liveThreadId === threadId) {
    _liveTimer = setTimeout(() => _pollLiveUpdates(threadId, myGen), 30000);
  }
}

export async function loadMoreLiveUpdates(threadId, after) {
  if (state.loading) return;
  state.loading = true;
  sentinel.classList.add('loading');
  try {
    const res  = await fetch(`/api/live/${encodeURIComponent(threadId)}/updates?after=${encodeURIComponent(after)}`);
    const data = await res.json();
    if (!state.liveMode || state.liveThreadId !== threadId) return;
    if (res.ok && data.updates?.length) {
      const container = feed.querySelector('.live-updates');
      if (container) container.insertAdjacentHTML('beforeend', data.updates.map(u => renderLiveUpdate(u)).join(''));
    }
    state.liveAfter = res.ok ? (data.after || null) : null;
    sentinel.classList.remove('loading');
  } catch { sentinel.classList.remove('loading'); }
  finally  { state.loading = false; }
}

export async function loadMoreComments(btn) {
  const sub    = btn.dataset.sub;
  const postId = btn.dataset.post;
  const ids    = btn.dataset.ids;
  const depth  = parseInt(btn.dataset.depth, 10) || 0;
  const wrap   = btn.closest('.more-comments-wrap');
  if (!wrap) return;
  btn.disabled = true;
  btn.textContent = 'Loading…';
  try {
    const url = `/api/r/${encodeURIComponent(sub)}/morechildren/${encodeURIComponent(postId)}?children=${encodeURIComponent(ids)}&sort=${state.currentCommentSort}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (!res.ok) { btn.textContent = 'Failed to load'; btn.disabled = false; return; }
    if (!data.comments?.length) { wrap.remove(); return; }
    const html = renderCommentTree(data.comments, depth, sub, postId, state._pvData?.post?.author || '');
    wrap.insertAdjacentHTML('afterend', html);
    initVideos(wrap.parentElement);
    initRedgifs(wrap.parentElement);
    initImgurAlbums(wrap.parentElement);
    wrap.remove();
  } catch {
    btn.textContent = 'Failed to load';
    btn.disabled = false;
  }
}
