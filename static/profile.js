import { state } from './state.js';
import { escHtml, fmtNum, fmtDate, errState, buildTimeFilterHtml } from './utils.js';
import { renderPost, renderUserCommentCard, waitForMdLibs } from './render.js';
import { initMedia, initGifVideos } from './media.js';
import { showSkeletons, setMainOpen } from './feed.js';

const feed      = document.getElementById('feed');
const sentinel  = document.getElementById('scroll-sentinel');
const sortBar   = document.getElementById('sort-bar');
const ctxInfo   = document.getElementById('ctx-info');
const subInput   = document.getElementById('subreddit-input');
const pvSubInput = document.getElementById('pv-subreddit-input');

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
  const sidebarBtn = `<button class="sidebar-toggle" id="sidebar-toggle-btn" aria-expanded="false">about</button>`;
  return tabBtns + `<div style="display:flex;align-items:center;border-left:1px solid var(--b);margin-left:4px;padding-left:8px;gap:2px">` + sortBtns + `</div>` + (sort==='top' ? buildTimeFilterHtml(time) : '') + sidebarBtn;
}

// Archived (Arctic Shift) fetches go through a slower third-party API than a
// normal Reddit call. If a fresh profile load is still pending after this long,
// assume that's what's happening and say so, rather than leaving skeletons up
// with no explanation.
const ARCHIVE_NOTICE_DELAY = 700;

export async function loadProfileTab(username, tab, sort='new', time='all', after=null, append=false, injectedData=null) {
  if (append && state.loading) return;
  if (!append) state.feedGen++;
  const myGen = state.feedGen;
  state.loading = true;
  let noticeTimer = null;
  if (!append) {
    showSkeletons();
    state.profileAfter = null;
    noticeTimer = setTimeout(() => {
      if (myGen === state.feedGen) {
        feed.insertAdjacentHTML('afterbegin',
          `<div class="thread-banner archive-loading-notice">Taking a moment — checking archived history for u/${escHtml(username)}…</div>`);
      }
    }, ARCHIVE_NOTICE_DELAY);
  }
  else sentinel.classList.add('loading');
  try {
    let data, res;
    if (injectedData) {
      data = injectedData;
      res = { ok: true };
    } else {
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
      res  = await fetch(url);
      data = await res.json();
    }
    clearTimeout(noticeTimer);
    if (myGen !== state.feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error||'Error'), 'feed');
      return;
    }
    await waitForMdLibs();
    if (myGen !== state.feedGen) return;
    if (!append) feed.innerHTML = '';

    if (!append && data.archived) {
      feed.insertAdjacentHTML('beforeend',
        `<div class="thread-banner">u/${escHtml(username)}'s post history is not available. Data is from Arctic Shift</div>`);
    }

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
      initMedia(tmp);
      while (tmp.firstChild) feed.appendChild(tmp.firstChild);
      initGifVideos(feed);
      if (data.archived) {
        const postIds = items.filter(i => i.type === 'post').map(i => i.data.id);
        refreshArchivedLiveInfo(postIds, myGen);
      }
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
        initMedia(tmp);
        while (tmp.firstChild) feed.appendChild(tmp.firstChild);
        initGifVideos(feed);
        if (data.archived) refreshArchivedLiveInfo(items.map(p => p.id), myGen);
      } else {
        feed.insertAdjacentHTML('beforeend', items.map((c,i)=>renderUserCommentCard(c,startIdx+i)).join(''));
      }
    }
    state.profileAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === state.feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { clearTimeout(noticeTimer); if (myGen === state.feedGen) state.loading = false; }
}

// Arctic Shift's score/comment-count is a stale snapshot from whenever it first
// crawled the post. Fetch current numbers from Reddit in the background and
// patch cards in place, instead of blocking the (already slow) archived load on it.
async function refreshArchivedLiveInfo(postIds, myGen) {
  if (!postIds.length) return;
  try {
    const res = await fetch(`/api/posts/live-info?ids=${postIds.map(encodeURIComponent).join(',')}`);
    if (!res.ok || myGen !== state.feedGen) return;
    const info = await res.json();
    for (const [id, live] of Object.entries(info)) applyLiveInfo(id, live);
  } catch {}
}

function applyLiveInfo(id, live) {
  const card = feed.querySelector(`[data-post-id="${id}"]`);
  if (!card) return;
  const scoreNum = card.querySelector('.score-num');
  if (scoreNum) scoreNum.textContent = fmtNum(live.score);
  const minScore = card.querySelector('.min-score');
  if (minScore) {
    const svg = minScore.querySelector('svg');
    minScore.innerHTML = (svg ? svg.outerHTML : '') + fmtNum(live.score);
  }
  const commentsLink = card.querySelector('.comments-link');
  if (commentsLink) {
    const svg = commentsLink.querySelector('svg');
    commentsLink.innerHTML = (svg ? svg.outerHTML : '') + ` ${fmtNum(live.num_comments)} comments`;
  }
  const minComments = card.querySelector('.min-comments');
  if (minComments) minComments.textContent = `${fmtNum(live.num_comments)} comments`;
}

function _renderProfileAbout(d) {
  document.getElementById('ctx-icon-wrap').innerHTML = d.icon
    ? `<img class="ctx-icon" src="${escHtml(d.icon)}" alt="" onerror="this.style.display='none'">` : '';
  document.getElementById('ctx-title').textContent = `u/${d.name}`;
  document.getElementById('ctx-stats').innerHTML =
    `<span>${fmtNum(d.karma_post)}</span> post karma · <span>${fmtNum(d.karma_comment)}</span> comment karma · joined ${fmtDate(d.created_utc)}`;
  ctxInfo.classList.add('visible');
}

export async function loadProfile(username, after=null) {
  state.profileMode = true; state.profileUser = username; state.profileTab = 'overview'; state.profileSort = 'new'; state.profileTime = 'all'; state.profileAfter = null;
  sortBar.style.display = 'none';
  ctxInfo.classList.remove('visible');
  subInput.value = '';
  pvSubInput.value = '';
  document.title = `u/${username} — RDVWR`;
  setMainOpen(`https://www.reddit.com/user/${encodeURIComponent(username)}/`);
  sortBar.innerHTML = buildProfileSortHtml(state.profileTab, state.profileSort, state.profileTime);
  sortBar.style.display = 'flex';

  const inj = !after && window.__INITIAL_PROFILE__;
  if (inj && inj._username === username.toLowerCase()) {
    window.__INITIAL_PROFILE__ = null;
    await loadProfileTab(username, 'overview', state.profileSort, state.profileTime, after, false, inj);
    if (inj._about) _renderProfileAbout(inj._about);
    return;
  }

  const aboutFetch = fetch(`/api/user/${encodeURIComponent(username)}/about`);
  const [, aboutRes] = await Promise.all([
    loadProfileTab(username, 'overview', state.profileSort, state.profileTime, after),
    aboutFetch
  ]);
  try {
    if (aboutRes.ok) {
      const d = await aboutRes.json();
      _renderProfileAbout(d);
    }
  } catch {}
}
