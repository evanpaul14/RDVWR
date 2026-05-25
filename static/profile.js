import { state } from './state.js';
import { escHtml, fmtNum, fmtDate, errState, buildTimeFilterHtml } from './utils.js';
import { renderPost, renderUserCommentCard } from './render.js';
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
  return tabBtns + `<div style="display:flex;align-items:center;border-left:1px solid var(--b);margin-left:4px;padding-left:8px;gap:2px">` + sortBtns + `</div>` + (sort==='top' ? buildTimeFilterHtml(time) : '');
}

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
      initMedia(tmp);
      while (tmp.firstChild) feed.appendChild(tmp.firstChild);
      initGifVideos(feed);
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
