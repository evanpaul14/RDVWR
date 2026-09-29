// Commenting as the logged-in Reddit account (see reddit_login.py). The forms only exist
// while the server says we're logged in, which it does only for requests from its own machine.
import { state } from './state.js';
import { accountActive } from './vote.js';

/** A comment box; `parent` is the fullname being replied to (t3_ post or t1_ comment). */
export function commentFormHtml(parent, label = 'comment') {
  return `<form class="comment-form" data-parent="${parent}">
    <textarea name="text" rows="3" maxlength="10000" placeholder="Write a ${label}…"></textarea>
    <div class="comment-form-row"><button type="submit">${label}</button>
      <label class="comment-attach" title="Attach an image or GIF">📎 <span class="comment-attach-name">image</span><input type="file" name="image" accept="image/*" hidden></label>
      <span class="comment-form-msg" role="status"></span></div>
  </form>`;
}

export const replyBtnHtml = id =>
  accountActive() ? `<div class="comment-actions"><button type="button" class="reply-btn" data-reply-to="t1_${id}">reply</button></div>` : '';

function insertComment(form, comment, renderTree) {
  const parentEl = form.closest('.comment');
  if (parentEl) {
    const html = renderTree([comment], +parentEl.dataset.depth + 1, state._pvSub, state._pvPostId, '');
    let replies = parentEl.querySelector(':scope > .comment-replies');
    if (!replies) {
      replies = document.createElement('div');
      replies.className = 'comment-replies';
      parentEl.appendChild(replies);
    }
    replies.insertAdjacentHTML('afterbegin', html);
    return;
  }
  const area = form.closest('.pv-comments-area') || document;
  area.querySelector(':scope > .state')?.remove();
  let wrap = area.querySelector('.pv-comments');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'pv-comments';
    form.after(wrap);
  }
  wrap.insertAdjacentHTML('afterbegin', renderTree([comment], 0, state._pvSub, state._pvPostId, ''));
}

async function submitForm(form, renderTree) {
  const ta = form.elements.text;
  const text = ta.value.trim();
  const file = form.elements.image.files[0];
  const msg = form.querySelector('.comment-form-msg');
  if ((!text && !file) || form.dataset.busy) return;
  form.dataset.busy = '1';
  msg.textContent = file ? 'Uploading…' : 'Posting…';
  try {
    let media_id;
    if (file) {
      const fd = new FormData();
      fd.append('file', file);
      const up = await fetch('/api/upload', { method: 'POST', body: fd });
      const upData = await up.json().catch(() => ({}));
      if (!up.ok) throw new Error(upData.error || `Upload failed (HTTP ${up.status})`);
      media_id = upData.asset_id;
      msg.textContent = 'Posting…';
    }
    const res = await fetch('/api/comment', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent: form.dataset.parent, text, media_id }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    insertComment(form, data.comment, renderTree);
    ta.value = '';
    form.elements.image.value = '';
    form.querySelector('.comment-attach-name').textContent = 'image';
    msg.textContent = '';
    if (form.dataset.parent.startsWith('t1_')) form.remove();
  } catch (err) {
    msg.textContent = `Failed: ${err.message}`;
  } finally {
    delete form.dataset.busy;
  }
}

/** Delegated handlers for reply buttons and comment forms. `renderTree` is render.js's
 *  renderCommentTree (passed in to keep this module free of a render.js import cycle). */
export function initCommenting(renderTree) {
  document.addEventListener('click', e => {
    const btn = e.target.closest('.reply-btn[data-reply-to]');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const actions = btn.closest('.comment-actions');
    const open = actions.nextElementSibling?.matches('.comment-form') ? actions.nextElementSibling : null;
    if (open) { open.remove(); return; }
    actions.insertAdjacentHTML('afterend', commentFormHtml(btn.dataset.replyTo, 'reply'));
    actions.nextElementSibling.elements.text.focus();
  }, true);
  document.addEventListener('change', e => {
    const input = e.target.closest?.('.comment-form input[type="file"]');
    if (input) input.closest('.comment-attach').querySelector('.comment-attach-name').textContent = input.files[0]?.name || 'image';
  });
  document.addEventListener('submit', e => {
    const form = e.target.closest?.('.comment-form[data-parent]');
    if (!form) return;
    e.preventDefault();
    submitForm(form, renderTree);
  }, true);
}
