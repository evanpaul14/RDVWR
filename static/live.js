import { state } from './state.js';
import { escHtml, fmtNum, errState } from './utils.js';
import { renderLiveUpdate, renderMd } from './render.js';
import { showSkeletons, setMainOpen } from './feed.js';

const feed      = document.getElementById('feed');
const sentinel  = document.getElementById('scroll-sentinel');
const sortBar   = document.getElementById('sort-bar');
const ctxInfo   = document.getElementById('ctx-info');
const subInput   = document.getElementById('subreddit-input');
const pvSubInput = document.getElementById('pv-subreddit-input');

let _liveTimer = null;
let _liveResumeArgs = null;

export function cancelLivePoll() {
  if (_liveTimer) { clearTimeout(_liveTimer); _liveTimer = null; }
  _liveResumeArgs = null;
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (_liveTimer) { clearTimeout(_liveTimer); _liveTimer = null; }
  } else if (_liveResumeArgs && !_liveTimer) {
    _liveTimer = setTimeout(() => _pollLiveUpdates(..._liveResumeArgs), 0);
  }
});

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
      _liveResumeArgs = [threadId, myGen];
      if (!document.hidden) _liveTimer = setTimeout(() => _pollLiveUpdates(threadId, myGen), 30000);
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
    _liveResumeArgs = [threadId, myGen];
    if (!document.hidden) _liveTimer = setTimeout(() => _pollLiveUpdates(threadId, myGen), 30000);
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
