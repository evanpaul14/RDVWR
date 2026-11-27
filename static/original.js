// "view original" buttons next to *edited marks: fetch the text as the Arctic Shift archive
// first captured it (GET /api/archive/original/<kind>/<id>, usually seconds after posting,
// before any edit) and show a word diff from that to the current text under the body.
import { escHtml, timeAgo, fmtDateTime } from './utils.js';
import { wordDiff } from './textdiff.js';

// Current markdown of the edited comments/posts on the page, by id, for diffing against.
const current = new Map();

/** The button for an edited comment or self post; remembers its current text. */
export function originalBtnHtml(kind, id, body) {
  current.set(`${kind}:${id}`, body || '');
  return ` <button type="button" class="restore-btn original-btn" data-original="${kind}:${escHtml(id)}" title="Compare with the original text from the Arctic Shift archive">view original</button>`;
}

const OP_TAG = { '-': 'del', '+': 'ins' };

/** The diff panel's HTML for `original` ({body, retrieved_utc}) against `now`. */
export function diffPanelHtml(original, now) {
  const captured = original.retrieved_utc
    ? ` <span title="${fmtDateTime(original.retrieved_utc)}">captured ${timeAgo(original.retrieved_utc)}</span>` : '';
  if (original.body.trim() === now.trim()) {
    return `<div class="edit-diff"><div class="edit-diff-head">The archive${captured} only has the edited text, so there's no earlier version to compare.</div></div>`;
  }
  const text = wordDiff(original.body, now)
    .map(([op, t]) => OP_TAG[op] ? `<${OP_TAG[op]}>${escHtml(t)}</${OP_TAG[op]}>` : escHtml(t)).join('');
  return `<div class="edit-diff">
    <div class="edit-diff-head">Changes since the archived original${captured}: <del>removed</del> <ins>added</ins></div>
    <div class="edit-diff-text">${text}</div>
  </div>`;
}

/** Where the panel goes: after the comment's body, or after the post's body. */
function anchorFor(btn, kind) {
  if (kind === 'comment') return btn.closest('.comment')?.querySelector(':scope > .comment-body');
  const scope = btn.closest('#pv-content') || document;
  return scope.querySelector('.pv-body') || scope.querySelector('.pv-meta');
}

async function showOriginal(btn) {
  if (btn.dataset.busy) return;
  const [kind, id] = btn.dataset.original.split(':');
  const anchor = anchorFor(btn, kind);
  if (!anchor) return;
  const open = anchor.nextElementSibling?.classList.contains('edit-diff') ? anchor.nextElementSibling : null;
  if (open) {   // second click hides it
    open.remove();
    btn.textContent = 'view original';
    return;
  }
  btn.dataset.busy = '1';
  btn.textContent = 'loading…';
  try {
    const res = await fetch(`/api/archive/original/${kind}/${encodeURIComponent(id)}`,
      { signal: AbortSignal.timeout(20000) });
    const data = await res.json().catch(() => ({}));
    if (res.status === 404) {
      btn.textContent = 'not archived';
      btn.disabled = true;
      return;
    }
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    anchor.insertAdjacentHTML('afterend', diffPanelHtml(data, current.get(`${kind}:${id}`) ?? ''));
    btn.textContent = 'hide original';
  } catch {
    btn.textContent = 'failed — retry';
  } finally {
    delete btn.dataset.busy;
  }
}

document.addEventListener('click', e => {
  const btn = e.target.closest('[data-original]');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  showOriginal(btn);
}, true);
