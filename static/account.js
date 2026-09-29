// The logged-in Reddit account's own profile (see reddit_login.py): its posts and comments with
// edit and delete, profile picture, sign out. Only reachable while the server says we're logged
// in, which it does only for requests from its own machine.
import { state } from './state.js';
import { escHtml, fmtNum, fmtDate, errState, timeAgo } from './utils.js';
import { accountActive } from './vote.js';
import { showSkeletons, setMainOpen } from './feed.js';

const feed       = document.getElementById('feed');
const sentinel   = document.getElementById('scroll-sentinel');
const sortBar    = document.getElementById('sort-bar');
const ctxInfo    = document.getElementById('ctx-info');
const subInput   = document.getElementById('subreddit-input');
const pvSubInput = document.getElementById('pv-subreddit-input');

const PENCIL = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
const me = () => window.__REDDIT_LOGIN__?.username;

const avatarInner = (icon, cls) => icon
  ? `<img class="${cls}" src="${escHtml(icon)}" alt="" data-onerror="hide">`
  : `<span class="${cls} acct-avatar-letter">${escHtml((me() || '?')[0].toUpperCase())}</span>`;

/** The header link to the account page: just the profile picture. */
export function initAccountLink() {
  const link = document.getElementById('account-btn');
  if (!link || !accountActive()) return;
  link.innerHTML = avatarInner('', 'acct-avatar');
  link.hidden = false;
  fetchAbout().then(d => setAvatars(d?.icon));
}

/** Show `icon` everywhere the account's picture appears (header, profile page). */
function setAvatars(icon) {
  const link = document.getElementById('account-btn');
  if (link) link.innerHTML = avatarInner(icon, 'acct-avatar');
  const big = feed.querySelector('.acct-avatar-slot');
  if (big) big.innerHTML = avatarInner(icon, 'acct-avatar acct-avatar-lg');
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : body instanceof FormData ? { method: 'POST', body } :
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (HTTP ${res.status})`);
  return data;
}

function panelHtml() {
  return `<div class="acct-panel">
    <div class="acct-row acct-head">
      <div class="acct-avatar-wrap">
        <span class="acct-avatar-slot">${avatarInner('', 'acct-avatar acct-avatar-lg')}</span>
        <button type="button" class="acct-pencil" data-acct-pencil aria-label="Change profile picture" aria-haspopup="menu" aria-expanded="false">${PENCIL}</button>
        <div class="acct-menu" role="menu" hidden>
          <label class="acct-menu-item" role="menuitem">Upload new picture<input type="file" data-acct-avatar accept="image/jpeg,image/png" hidden></label>
          <button type="button" class="acct-menu-item" role="menuitem" data-acct-avatar-remove>Remove picture</button>
        </div>
      </div>
      <div class="acct-head-links">
        <a class="settings-action-btn" href="/user/${encodeURIComponent(me())}" data-nav="/user/${encodeURIComponent(me())}">Public profile</a>
        <form method="post" action="/auth/reddit/logout"><input type="hidden" name="next" value="/"><button class="settings-action-btn" type="submit">Sign out</button></form>
      </div>
    </div>
    <div class="acct-msg" role="status"></div>
  </div>`;
}

function tabsHtml(tab) {
  return `<div class="acct-tabs">${['posts', 'comments'].map(t =>
    `<button type="button" class="sort-btn${t === tab ? ' active' : ''}" data-acct-tab="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}</div>`;
}

function itemHtml(kind, it) {
  const isPost = kind === 'posts';
  const fullname = (isPost ? 't3_' : 't1_') + it.id;
  const sub = escHtml(it.subreddit);
  const path = isPost ? `/r/${sub}/comments/${escHtml(it.id)}` : `/r/${sub}/comments/${escHtml(it.link_id)}/_/${escHtml(it.id)}`;
  const raw = isPost ? it.selftext : it.body;
  const head = isPost
    ? `<a class="acct-item-title" href="${path}" data-nav="${path}">${escHtml(it.title)}</a>
       <div class="acct-item-meta">r/${sub} · ▲ ${fmtNum(it.score)} · ${fmtNum(it.num_comments)} comments · ${timeAgo(it.created_utc)}</div>`
    : `<div class="acct-item-meta">in <a href="/r/${sub}" data-nav="/r/${sub}">r/${sub}</a> · <a href="${path}" data-nav="${path}">${escHtml(it.link_title)}</a></div>
       <div class="acct-item-text" data-acct-text>${escHtml(raw)}</div>
       <div class="acct-item-meta">▲ ${fmtNum(it.score)} · ${timeAgo(it.created_utc)}</div>`;
  const editable = !isPost || it.is_self;
  return `<div class="acct-item" data-acct-id="${fullname}">${head}
    ${isPost && it.is_self && raw ? `<div class="acct-item-text" data-acct-text>${escHtml(raw)}</div>` : ''}
    <div class="acct-manage">
      ${editable ? '<button type="button" class="acct-link" data-acct-edit>edit</button>' : ''}
      <button type="button" class="acct-link" data-acct-delete>delete</button>
      <span class="acct-msg" role="status"></span>
    </div></div>`;
}

async function fetchAbout() {
  try { return await api('/api/account'); } catch { return null; }
}

function renderAbout(d) {
  document.getElementById('ctx-icon-wrap').innerHTML = '';
  setAvatars(d?.icon);
  document.getElementById('ctx-title').textContent = `u/${me()}`;
  document.getElementById('ctx-stats').innerHTML = d
    ? `<span>${fmtNum(d.karma_post)}</span> post karma · <span>${fmtNum(d.karma_comment)}</span> comment karma · joined ${fmtDate(d.created_utc)}` : '';
  ctxInfo.classList.add('visible');
}

async function loadItems(tab, after = null) {
  const myGen = state.feedGen;
  const list = feed.querySelector('.acct-list');
  const more = feed.querySelector('.acct-more');
  more?.remove();
  try {
    const data = await api(`/api/account/${tab}${after ? `?after=${encodeURIComponent(after)}` : ''}`);
    if (myGen !== state.feedGen) return;
    list.querySelector('.acct-loading')?.remove();
    const items = data[tab] || [];
    if (!items.length && !after) list.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">Nothing here</div></div>';
    list.insertAdjacentHTML('beforeend', items.map(it => itemHtml(tab, it)).join(''));
    if (data.after) list.insertAdjacentHTML('afterend', `<button type="button" class="settings-action-btn acct-more" data-acct-more="${escHtml(data.after)}">Load more</button>`);
  } catch (e) {
    if (myGen === state.feedGen) list.innerHTML = errState(escHtml(e.message), 'feed');
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
  sortBar.style.display = 'none';
  sentinel.innerHTML = '';
  sentinel.classList.remove('active', 'loading');
  ctxInfo.classList.remove('visible');
  feed.innerHTML = panelHtml() + tabsHtml(tab) + '<div class="acct-list"><div class="acct-loading state-sub">Loading…</div></div>';
  fetchAbout().then(d => { if (state.accountMode) renderAbout(d); });
  await loadItems(tab);
}

const say = (el, text) => { if (el) el.textContent = text; };

feed.addEventListener('click', async e => {
  if (!state.accountMode) return;
  const tabBtn = e.target.closest('[data-acct-tab]');
  if (tabBtn) {
    if (tabBtn.dataset.acctTab !== state.accountTab) {
      history.pushState({}, '', tabBtn.dataset.acctTab === 'posts' ? '/account' : '/account?tab=comments');
      loadAccount(tabBtn.dataset.acctTab);
    }
    return;
  }
  const menu = feed.querySelector('.acct-menu');
  const pencil = e.target.closest('[data-acct-pencil]');
  if (menu) {
    menu.hidden = pencil ? !menu.hidden : true;
    feed.querySelector('[data-acct-pencil]')?.setAttribute('aria-expanded', String(!menu.hidden));
  }
  if (pencil) return;
  const moreBtn = e.target.closest('[data-acct-more]');
  if (moreBtn) { moreBtn.disabled = true; loadItems(state.accountTab, moreBtn.dataset.acctMore); return; }

  if (e.target.closest('[data-acct-avatar-remove]')) {
    const msg = feed.querySelector('.acct-panel .acct-msg');
    try { say(msg, 'Removing…'); await api('/api/account/avatar/remove', {}); say(msg, 'Picture removed.'); fetchAbout().then(renderAbout); }
    catch (err) { say(msg, `Failed: ${err.message}`); }
    return;
  }

  const item = e.target.closest('.acct-item');
  if (!item) return;
  const id = item.dataset.acctId, msg = item.querySelector('.acct-msg'), manage = item.querySelector('.acct-manage');
  if (e.target.closest('[data-acct-edit]')) {
    if (item.querySelector('.acct-edit')) return;
    const textEl = item.querySelector('[data-acct-text]');
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
        if (textEl) textEl.textContent = text;
        else item.querySelector('.acct-item-meta:last-of-type')?.insertAdjacentHTML('afterend', `<div class="acct-item-text" data-acct-text>${escHtml(text)}</div>`);
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

feed.addEventListener('change', async e => {
  const input = e.target.closest('[data-acct-avatar]');
  if (!state.accountMode || !input || !input.files[0]) return;
  const msg = feed.querySelector('.acct-panel .acct-msg');
  const fd = new FormData();
  fd.append('file', input.files[0]);
  try { say(msg, 'Uploading…'); await api('/api/account/avatar', fd); say(msg, 'Picture updated (Reddit can take a moment to show it).'); fetchAbout().then(renderAbout); }
  catch (err) { say(msg, `Failed: ${err.message}`); }
  input.value = '';
});
