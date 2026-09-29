// New post as the logged-in Reddit account (see reddit_login.py): text, link, or media
// (one image, one video, or several images as a gallery), with optional flair. The header
// button only shows while the server says we're logged in (its own machine only).
import { accountActive } from './vote.js';

const MODAL = `
<div class="submit-overlay" id="submit-overlay"></div>
<form class="submit-modal" id="submit-modal" role="dialog" aria-modal="true" aria-label="New post">
  <div class="submit-title">New post</div>
  <input name="sub" class="settings-input" placeholder="r/subreddit" autocomplete="off" required>
  <input name="title" class="settings-input" placeholder="Title" maxlength="300" required>
  <div class="submit-flair" hidden>
    <select name="flair_id" class="settings-select"></select>
    <input name="flair_text" class="settings-input" placeholder="Custom flair text" maxlength="64" hidden>
  </div>
  <div class="submit-kind">
    <label><input type="radio" name="kind" value="self" checked> Text</label>
    <label><input type="radio" name="kind" value="link"> Link</label>
    <label><input type="radio" name="kind" value="media"> Image / video</label>
  </div>
  <textarea name="body" class="settings-input settings-textarea" rows="7" maxlength="40000" placeholder="Text (optional)"></textarea>
  <div class="submit-media" hidden>
    <input type="file" name="files" accept="image/*,video/mp4,video/quicktime" multiple>
    <div class="submit-media-hint">One image or video, or several images for a gallery.</div>
  </div>
  <div class="submit-row"><button type="submit" class="settings-action-btn">Post</button><button type="button" class="settings-action-btn" data-close>Cancel</button><span class="submit-msg" role="status"></span></div>
</form>`;

async function jsonOrError(res, what) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${what} failed (HTTP ${res.status})`);
  return data;
}

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
    const flairBox = form.querySelector('.submit-flair');
    const flairSel = form.elements.flair_id;
    let flairs = [], flairRequired = false, flairFor = '';

    async function loadFlairs() {
      const sub = form.elements.sub.value.trim().replace(/^r\//, '');
      if (!/^[A-Za-z0-9_]{1,50}$/.test(sub) || sub === flairFor) return;
      flairFor = sub;
      flairBox.hidden = true;
      try {
        const data = await jsonOrError(await fetch(`/api/r/${encodeURIComponent(sub)}/flairs`), 'Flair lookup');
        if (flairFor !== sub) return;
        flairs = data.flairs; flairRequired = data.required;
        flairSel.innerHTML = `<option value="">${flairRequired ? 'Choose a flair (required)' : 'No flair'}</option>` +
          flairs.map(f => `<option value="${f.id}">${(f.text || '(blank)').replace(/</g, '&lt;')}</option>`).join('');
        flairBox.hidden = !flairs.length;
        syncFlairText();
      } catch { flairBox.hidden = true; }
    }
    function syncFlairText() {
      const f = flairs.find(x => x.id === flairSel.value);
      form.elements.flair_text.hidden = !f?.editable;
      form.elements.flair_text.value = f?.editable ? f.text : '';
    }

    const sub = getSub();
    if (sub && !['popular', 'all'].includes(sub.toLowerCase()) && !sub.includes('+')) form.elements.sub.value = sub;
    (form.elements.sub.value ? form.elements.title : form.elements.sub).focus();
    loadFlairs();

    const syncKind = () => {
      const kind = form.elements.kind.value;
      form.elements.body.hidden = kind === 'media';
      form.querySelector('.submit-media').hidden = kind !== 'media';
      form.elements.body.placeholder = kind === 'link' ? 'https://…' : 'Text (optional)';
      form.elements.body.rows = kind === 'link' ? 2 : 7;
    };
    form.addEventListener('change', e => {
      if (e.target === flairSel) syncFlairText();
      else if (e.target.name === 'kind') syncKind();
    });
    form.elements.sub.addEventListener('change', loadFlairs);
    root.querySelector('#submit-overlay').addEventListener('click', close);
    form.querySelector('[data-close]').addEventListener('click', close);
    form.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });

    form.addEventListener('submit', async e => {
      e.preventDefault();
      if (form.dataset.busy) return;
      form.dataset.busy = '1';
      try {
        let kind = form.elements.kind.value, media = [];
        if (kind === 'media') {
          const files = [...form.elements.files.files];
          if (!files.length) throw new Error('Choose at least one image or video.');
          const hasVideo = files.some(f => f.type.startsWith('video/'));
          if (hasVideo && files.length > 1) throw new Error('A video post takes a single video.');
          kind = hasVideo ? 'video' : files.length === 1 ? 'image' : 'gallery';
          for (const [i, file] of files.entries()) {
            msg.textContent = `Uploading ${i + 1}/${files.length}…`;
            const fd = new FormData();
            fd.append('file', file);
            media.push(await jsonOrError(await fetch('/api/upload', { method: 'POST', body: fd }), 'Upload'));
          }
        }
        if (flairRequired && !flairSel.value) throw new Error('This subreddit requires a flair.');
        msg.textContent = 'Posting…';
        const data = await jsonOrError(await fetch('/api/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sub: form.elements.sub.value, title: form.elements.title.value, kind, media,
            body: form.elements.body.value, flair_id: flairSel.value, flair_text: form.elements.flair_text.value }) }), 'Post');
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
