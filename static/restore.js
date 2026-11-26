// "restore" buttons on deleted/removed comments: fetch the comment's original author and text
// from the Arctic Shift archive (GET /api/archive/comments) and put them in place of "[deleted]".
// The archive only has text it captured before the deletion, so some comments can't come back.
import { escHtml, userLink } from './utils.js';
import { renderMd } from './render.js';
import { translateBtnHtml } from './translate.js';

export const restoreBtnHtml = id =>
  `<button type="button" class="restore-btn" data-restore="${escHtml(id)}" title="Load the original text from the Arctic Shift archive">restore</button>`;
export const ARCHIVED_MARK = '<span class="restored-mark" title="Deleted on Reddit; text from the Arctic Shift archive">archived</span>';

function apply(comment, c) {
  const header = comment.querySelector(':scope > .comment-header');
  const author = header.querySelector('.comment-author');
  if (author && c.author !== '[deleted]') author.outerHTML = userLink(c.author, author.className);
  comment.querySelector(':scope > .comment-body').innerHTML = renderMd(c.body);
  header.querySelector('.restore-btn').outerHTML = translateBtnHtml(c.lang) + ARCHIVED_MARK;
  comment.classList.remove('comment-deleted');
  comment.classList.add('comment-restored');
}

async function restore(btn) {
  if (btn.dataset.busy) return;
  const comment = btn.closest('.comment');
  if (!comment) return;
  btn.dataset.busy = '1';
  btn.textContent = 'restoring…';
  try {
    const res = await fetch(`/api/archive/comments?ids=${encodeURIComponent(btn.dataset.restore)}`,
      { signal: AbortSignal.timeout(20000) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    const c = data.comments?.[btn.dataset.restore];
    if (c) { apply(comment, c); return; }
    btn.textContent = 'not archived';
    btn.disabled = true;
  } catch {
    btn.textContent = 'restore failed — retry';
  } finally {
    delete btn.dataset.busy;
  }
}

document.addEventListener('click', e => {
  const btn = e.target.closest('[data-restore]');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  restore(btn);
}, true);
