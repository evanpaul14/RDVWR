// Translate buttons on the post (title + body) and on comments. Text goes to POST /api/translate;
// the result replaces the element's contents as plain text, and a second click restores the original.
const escLang = () => (navigator.language || 'en').split('-')[0];
const NORM = { nb: 'no', nn: 'no', iw: 'he' };
const deviceLang = () => NORM[escLang()] || escLang();

/** `lang` is the server-detected language: a code, null (too short/uncertain), or undefined
 *  (no detector on the server). data-foreign decides whether auto mode shows the button; the
 *  translateButton setting (body classes, see applySettings) can force it on or off. */
export const translateBtnHtml = (lang, cls = 'translate-btn') =>
  `<button type="button" class="${cls}" data-translate data-foreign="${lang === undefined || (lang !== null && lang !== deviceLang()) ? 1 : 0}" title="Translate to ${escLang()}">translate</button>`;
const originals = new WeakMap();

function targets(btn) {
  const comment = btn.closest('.comment');
  if (comment) return [comment.querySelector(':scope > .comment-body')];
  const root = btn.closest('#pv-content');
  return root ? [root.querySelector('.pv-title'), root.querySelector('.pv-body')] : [];
}

function restore(els, btn) {
  els.forEach(el => { if (originals.has(el)) { el.innerHTML = originals.get(el); originals.delete(el); el.classList.remove('translated'); } });
  btn.textContent = 'translate';
}

async function toggle(btn) {
  const els = targets(btn).filter(Boolean);
  if (!els.length || btn.dataset.busy) return;
  if (els.some(el => originals.has(el))) { restore(els, btn); return; }
  btn.dataset.busy = '1';
  btn.textContent = 'translating…';
  try {
    const res = await fetch('/api/translate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texts: els.map(el => el.innerText.trim()), to: escLang() }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    els.forEach((el, i) => {
      originals.set(el, el.innerHTML);
      el.textContent = data.texts[i];
      el.classList.add('translated');
    });
    btn.textContent = 'show original';
  } catch (err) {
    btn.textContent = 'translate failed — retry';
  } finally {
    delete btn.dataset.busy;
  }
}

document.addEventListener('click', e => {
  const btn = e.target.closest('[data-translate]');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  toggle(btn);
}, true);
