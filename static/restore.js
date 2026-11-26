// "restore" buttons on comments whose text or author was deleted: fetch the original author and
// text from the Arctic Shift archive (GET /api/archive/comments) and put them in place of
// "[deleted]". The archive only has what it captured before the deletion, so some can't come back.
import { escHtml, userLink } from './utils.js';
import { renderMd } from './render.js';
import { translateBtnHtml } from './translate.js';

export const restoreBtnHtml = id =>
  `<button type="button" class="restore-btn" data-restore="${escHtml(id)}" title="Load the original author and text from the Arctic Shift archive">restore</button>`;
export const ARCHIVED_MARK = '<span class="restored-mark" title="Deleted on Reddit; restored from the Arctic Shift archive">archived</span>';

/** Fills in whatever the archive has; false when it had nothing this comment is missing. */
function apply(comment, c) {
  const header = comment.querySelector(':scope > .comment-header');
  const author = header.querySelector('.comment-author');
  const btn = header.querySelector('.restore-btn');
  let extra = '';
  let changed = false;
  if (author?.textContent === '[deleted]' && c.author !== '[deleted]') {
    author.outerHTML = userLink(c.author, author.className);
    changed = true;
  }
  if (comment.classList.contains('comment-deleted') && c.body) {
    comment.querySelector(':scope > .comment-body').innerHTML = renderMd(c.body);
    comment.classList.remove('comment-deleted');
    extra = translateBtnHtml(c.lang);
    changed = true;
  }
  if (!changed) return false;
  btn.outerHTML = extra + ARCHIVED_MARK;
  comment.classList.add('comment-restored');
  return true;
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
    if (c && apply(comment, c)) return;
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
