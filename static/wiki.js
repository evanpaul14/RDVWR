import { state } from './state.js';
import { escHtml, fmtDate, errState } from './utils.js';
import { setMainOpen } from './feed.js';

const feed    = document.getElementById('feed');
const sortBar = document.getElementById('sort-bar');

export async function loadWikiPage(sub, page) {
  state._wikiSub = sub; state._wikiPage = page;
  state.wikiMode = true; state.afterToken = null;
  setMainOpen(`https://www.reddit.com/r/${encodeURIComponent(sub)}/wiki/${encodeURIComponent(page)}`);
  feed.innerHTML = '<div class="state"><div class="state-icon">⌗</div><div class="state-title">Loading…</div></div>';
  sortBar.innerHTML = `<a class="sort-btn" href="/r/${escHtml(sub)}" data-nav="/r/${escHtml(sub)}">← r/${escHtml(sub)}</a>`;
  sortBar.style.display = 'flex';
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
          <span class="wiki-sub"><a href="/r/${escHtml(sub)}" data-nav="/r/${escHtml(sub)}">r/${escHtml(sub)}</a></span>
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
