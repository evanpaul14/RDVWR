// The logged-in Reddit account's own profile (see reddit_login.py): its posts and comments with
// edit and delete, profile picture, sign out. Only reachable while the server says we're logged
// in, which it does only for requests from its own machine.
import { state } from './state.js';
import { escHtml, fmtNum, fmtDate, errState, timeAgo } from './utils.js';
import { accountActive } from './vote.js';
import { showSkeletons, setMainOpen } from './feed.js';
import { renderPost, renderUserCommentCard, renderMd, waitForMdLibs } from './render.js';
import { initMedia, initGifVideos } from './media.js';

const feed       = document.getElementById('feed');
const sentinel   = document.getElementById('scroll-sentinel');
const sortBar    = document.getElementById('sort-bar');
const ctxInfo    = document.getElementById('ctx-info');
const subInput   = document.getElementById('subreddit-input');
const pvSubInput = document.getElementById('pv-subreddit-input');

const PENCIL = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
const me = () => window.__REDDIT_LOGIN__?.username;

const avatarInner = icon => icon
  ? `<img class="ctx-icon" src="${escHtml(icon)}" alt="" data-onerror="hide">`
  : `<span class="ctx-icon acct-avatar-letter">${escHtml((me() || '?')[0].toUpperCase())}</span>`;

/** The header link to the account page: a generic profile icon (the real picture is on the page itself). */
export function initAccountLink() {
  const link = document.getElementById('account-btn');
  if (!link || !accountActive()) return;
  link.innerHTML = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="8" r="4" stroke="currentColor" stroke-width="1.5"/><path d="M4 20c0-4 3.6-6 8-6s8 2 8 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
  link.hidden = false;
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : body instanceof FormData ? { method: 'POST', body } :
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (HTTP ${res.status})`);
  return data;
}

/** The picture with its pencil menu, in the title strip where other profiles show theirs. */
function avatarHtml(icon) {
  return `<span class="acct-avatar-wrap">${avatarInner(icon)}
    <button type="button" class="acct-pencil" data-acct-pencil aria-label="Change profile picture" aria-haspopup="menu" aria-expanded="false">${PENCIL}</button>
    <span class="acct-menu" role="menu" hidden>
      <label class="acct-menu-item" role="menuitem">Upload new picture<input type="file" data-acct-avatar accept="image/jpeg,image/png" hidden></label>
      <button type="button" class="acct-menu-item" role="menuitem" data-acct-avatar-remove>Remove picture</button>
    </span></span>`;
}

const actionsHtml = () => `<div class="acct-ctx-actions" id="acct-ctx-actions">
  <div class="acct-head-links">
    <a class="settings-action-btn" href="/user/${encodeURIComponent(me())}" data-nav="/user/${encodeURIComponent(me())}">View public profile</a>
    <form method="post" action="/auth/reddit/logout"><input type="hidden" name="next" value="/"><button class="settings-action-btn" type="submit">Sign out</button></form>
  </div>
  <div class="acct-msg" role="status"></div>
</div>`;

const tabsHtml = tab => ['posts', 'comments'].map(t =>
  `<button type="button" class="sort-btn${t === tab ? ' active' : ''}" data-acct-tab="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('');

/** The same card other profiles show, with edit/delete underneath. */
function itemHtml(kind, it, idx) {
  const isPost = kind === 'posts';
  const card = isPost ? renderPost(it, idx, true) : renderUserCommentCard(it, idx);
  return `<div class="acct-item" data-acct-id="${isPost ? 't3_' : 't1_'}${escHtml(it.id)}">${card}
    <div class="acct-manage">
      ${!isPost || it.is_self ? '<button type="button" class="acct-link" data-acct-edit>edit</button>' : ''}
      <button type="button" class="acct-link" data-acct-delete>delete</button>
      <span class="acct-msg" role="status"></span>
    </div></div>`;
}

async function fetchAbout() {
  try { return await api('/api/account'); } catch { return null; }
}

function renderAbout(d) {
  const wrap = document.getElementById('ctx-icon-wrap');
  const menuOpen = wrap.querySelector('.acct-menu:not([hidden])');
  if (!wrap.querySelector('.acct-avatar-wrap') || !menuOpen) wrap.innerHTML = avatarHtml(d?.icon);
  document.getElementById('ctx-title').textContent = `u/${me()}`;
  document.getElementById('ctx-stats').innerHTML = d
    ? `<span>${fmtNum(d.karma_post)}</span> post karma · <span>${fmtNum(d.karma_comment)}</span> comment karma · joined ${fmtDate(d.created_utc)}` : '';
  if (!document.getElementById('acct-ctx-actions')) ctxInfo.insertAdjacentHTML('beforeend', actionsHtml());
  ctxInfo.classList.add('visible');
}

/** Leaving the account page: take its extras out of the shared title strip. */
export function leaveAccount() {
  document.getElementById('acct-ctx-actions')?.remove();
}

async function loadItems(tab, after = null) {
  const myGen = state.feedGen;
  feed.querySelector('.acct-more')?.remove();
  try {
    const data = await api(`/api/account/${tab}${after ? `?after=${encodeURIComponent(after)}` : ''}`);
    await waitForMdLibs();
    if (myGen !== state.feedGen) return;
    if (!after) feed.innerHTML = '';
    const items = data[tab] || [];
    if (!items.length && !after) { feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">Nothing here</div></div>'; return; }
    const start = feed.querySelectorAll('.acct-item').length;
    const tmp = document.createElement('div');
    tmp.innerHTML = items.map((it, i) => itemHtml(tab, it, start + i)).join('');
    initMedia(tmp);
    while (tmp.firstChild) feed.appendChild(tmp.firstChild);
    initGifVideos(feed);
    if (data.after) feed.insertAdjacentHTML('beforeend', `<button type="button" class="settings-action-btn acct-more" data-acct-more="${escHtml(data.after)}">Load more</button>`);
  } catch (e) {
    if (myGen === state.feedGen) feed.innerHTML = errState(escHtml(e.message), 'feed');
  }
}

export async function loadAccount(tab = 'posts') {
  state.accountMode = true;
  state.profileMode = state.homeMode = state.multiMode = state.searchMode = false;
  state.feedGen++;
  state.accountTab = tab;
  state.currentSub = '';
  document.title = 'Your profile — RDVWR';
  subInput.value = '';
  pvSubInput.value = '';
  setMainOpen('');
  sentinel.innerHTML = '';
  sentinel.classList.remove('active', 'loading');
  sortBar.innerHTML = tabsHtml(tab);
  sortBar.style.display = 'flex';
  showSkeletons();
  renderAbout(null);
  fetchAbout().then(d => { if (state.accountMode) renderAbout(d); });
  await loadItems(tab);
}

const say = (el, text) => { if (el) el.textContent = text; };

feed.addEventListener('click', async e => {
  if (!state.accountMode) return;
  const moreBtn = e.target.closest('[data-acct-more]');
  if (moreBtn) { moreBtn.disabled = true; loadItems(state.accountTab, moreBtn.dataset.acctMore); return; }

  const item = e.target.closest('.acct-item');
  if (!item) return;
  const id = item.dataset.acctId, msg = item.querySelector('.acct-msg'), manage = item.querySelector('.acct-manage');
  if (e.target.closest('[data-acct-edit]')) {
    if (item.querySelector('.acct-edit')) return;
    const textEl = item.querySelector('.ucc-body, .post-excerpt');
    const form = document.createElement('form');
    form.className = 'acct-edit';
    form.innerHTML = `<textarea class="settings-input settings-textarea" rows="6" maxlength="${id.startsWith('t3_') ? 40000 : 10000}"></textarea>
      <div class="acct-row"><button type="submit" class="submit-go">Save</button><button type="button" class="settings-action-btn" data-cancel>Cancel</button></div>`;
    const box = form.querySelector('textarea');
    box.disabled = true;
    manage.before(form);
    textEl?.setAttribute('hidden', '');
    const done = () => { form.remove(); textEl?.removeAttribute('hidden'); };
    try {   // listings cut post text short, so fetch the whole thing before it can be saved back
      box.value = (await api(`/api/account/text?id=${id}`)).text;
      box.disabled = false;
      box.focus();
    } catch (err) { say(msg, `Failed: ${err.message}`); done(); return; }
    form.querySelector('[data-cancel]').addEventListener('click', done);
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      const text = form.querySelector('textarea').value.trim();
      if (!text) return;
      try {
        say(msg, 'Saving…');
        await api('/api/account/edit', { id, text });
        if (textEl) textEl.innerHTML = textEl.classList.contains('ucc-body') ? renderMd(text) : `<div class="md">${renderMd(text)}</div>`;
        say(msg, 'Saved.');
        done();
      } catch (err) { say(msg, `Failed: ${err.message}`); }
    });
    return;
  }
  const del = e.target.closest('[data-acct-delete]');
  if (del) {
    // Two-step in place of a confirm() dialog: the first click arms the button.
    if (!del.dataset.armed) { del.dataset.armed = '1'; del.textContent = 'really delete?'; del.classList.add('acct-danger'); return; }
    try { say(msg, 'Deleting…'); await api('/api/account/delete', { id }); item.remove(); }
    catch (err) { say(msg, `Failed: ${err.message}`); delete del.dataset.armed; del.textContent = 'delete'; del.classList.remove('acct-danger'); }
  }
});

sortBar.addEventListener('click', e => {
  const tabBtn = state.accountMode && e.target.closest('[data-acct-tab]');
  if (!tabBtn || tabBtn.dataset.acctTab === state.accountTab) return;
  history.pushState({}, '', tabBtn.dataset.acctTab === 'posts' ? '/account' : '/account?tab=comments');
  loadAccount(tabBtn.dataset.acctTab);
});

// The picture menu and the header buttons live in the shared title strip, outside #feed.
const acctMsg = () => ctxInfo.querySelector('.acct-ctx-actions .acct-msg');
const setMenu = open => {
  const menu = ctxInfo.querySelector('.acct-menu');
  if (menu) menu.hidden = !open;
  ctxInfo.querySelector('[data-acct-pencil]')?.setAttribute('aria-expanded', String(!!open));
};

document.addEventListener('click', async e => {
  if (!state.accountMode) return;
  const pencil = e.target.closest('[data-acct-pencil]');
  if (pencil) { setMenu(ctxInfo.querySelector('.acct-menu')?.hidden); return; }
  if (!e.target.closest('.acct-menu')) { setMenu(false); return; }
  if (e.target.closest('[data-acct-avatar-remove]')) {
    setMenu(false);
    try { say(acctMsg(), 'Removing…'); await api('/api/account/avatar/remove', {}); say(acctMsg(), 'Picture removed.'); fetchAbout().then(renderAbout); }
    catch (err) { say(acctMsg(), `Failed: ${err.message}`); }
  }
});

document.addEventListener('change', async e => {
  const input = e.target.closest('[data-acct-avatar]');
  if (!state.accountMode || !input || !input.files[0]) return;
  const fd = new FormData();
  fd.append('file', input.files[0]);
  setMenu(false);
  try { say(acctMsg(), 'Uploading…'); await api('/api/account/avatar', fd); say(acctMsg(), 'Picture updated (Reddit can take a moment to show it).'); fetchAbout().then(renderAbout); }
  catch (err) { say(acctMsg(), `Failed: ${err.message}`); }
  input.value = '';
});
