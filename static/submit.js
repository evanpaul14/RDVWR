// New text/link post as the logged-in Reddit account (see reddit_login.py). The header
// button only shows while the server says we're logged in (its own machine only).
import { accountActive } from './vote.js';

const MODAL = `
<div class="submit-overlay" id="submit-overlay"></div>
<form class="submit-modal" id="submit-modal" role="dialog" aria-modal="true" aria-label="New post">
  <div class="submit-title">New post</div>
  <input name="sub" class="settings-input" placeholder="r/subreddit" autocomplete="off" required>
  <input name="title" class="settings-input" placeholder="Title" maxlength="300" required>
  <div class="submit-kind">
    <label><input type="radio" name="kind" value="self" checked> Text</label>
    <label><input type="radio" name="kind" value="link"> Link</label>
  </div>
  <textarea name="body" class="settings-input settings-textarea" rows="7" maxlength="40000" placeholder="Text (optional)"></textarea>
  <div class="submit-row"><button type="submit" class="settings-action-btn">Post</button><button type="button" class="settings-action-btn" data-close>Cancel</button><span class="submit-msg" role="status"></span></div>
</form>`;

/** `getSub` returns the subreddit currently being viewed (to prefill); `navigate` opens the new post. */
export function initSubmit({ getSub, navigate }) {
  const btn = document.getElementById('submit-btn');
  if (!btn || !accountActive()) return;
  btn.hidden = false;
  let root = null;

  const close = () => { root?.remove(); root = null; };

  function open() {
    if (root) return;
    root = document.createElement('div');
    root.innerHTML = MODAL;
    document.body.appendChild(root);
    const form = root.querySelector('#submit-modal');
    const msg = form.querySelector('.submit-msg');
    const sub = getSub();
    if (sub && !['popular', 'all'].includes(sub.toLowerCase()) && !sub.includes('+')) form.elements.sub.value = sub;
    (form.elements.sub.value ? form.elements.title : form.elements.sub).focus();
    const syncKind = () => {
      const link = form.elements.kind.value === 'link';
      form.elements.body.placeholder = link ? 'https://…' : 'Text (optional)';
      form.elements.body.rows = link ? 2 : 7;
    };
    form.addEventListener('change', syncKind);
    root.querySelector('#submit-overlay').addEventListener('click', close);
    form.querySelector('[data-close]').addEventListener('click', close);
    form.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    form.addEventListener('submit', async e => {
      e.preventDefault();
      if (form.dataset.busy) return;
      form.dataset.busy = '1';
      msg.textContent = 'Posting…';
      try {
        const res = await fetch('/api/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sub: form.elements.sub.value, title: form.elements.title.value,
            kind: form.elements.kind.value, body: form.elements.body.value }) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        close();
        navigate(data.path);
      } catch (err) {
        msg.textContent = `Failed: ${err.message}`;
        delete form.dataset.busy;
      }
    });
  }
  btn.addEventListener('click', open);
}
