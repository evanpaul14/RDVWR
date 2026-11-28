// Translate buttons on the post (title + body) and on comments. Text goes to POST /api/translate.
// Each run of inline content (a paragraph, list item, heading, ...) is sent as text with its inline
// elements replaced by numbered tags (`<a i=3>bold words</a>`, or `<a i=4></a>` for images, inline
// code and line breaks); the translator keeps those tags around the right words, so the result is
// rebuilt with the original formatting, links, spoilers and media. A second click restores the
// original nodes (moved back, not re-parsed, so players and listeners survive).
const escLang = () => (navigator.language || 'en').split('-')[0];
const NORM = { nb: 'no', nn: 'no', iw: 'he' };
const deviceLang = () => NORM[escLang()] || escLang();

/** `lang` is the server-detected language: a code, null (too short/uncertain), or undefined
 *  (no detector on the server). data-foreign decides whether auto mode shows the button; the
 *  translateButton setting (body classes, see applySettings) can force it on or off. */
export const translateBtnHtml = (lang, cls = 'translate-btn') =>
  `<button type="button" class="${cls}" data-translate data-foreign="${lang === undefined || (lang !== null && lang !== deviceLang()) ? 1 : 0}" title="Translate to ${escLang()}">translate</button>`;

// Kept as-is (moved, never re-created) and sent as an empty tag.
const ATOM = 'img,video,audio,iframe,picture,svg,canvas,object,embed,code,br,button,.md-video-embed,.md-media-link';
// Children that end an inline run; translated on their own.
const BLOCK = 'p,div,li,ul,ol,blockquote,h1,h2,h3,h4,h5,h6,table,thead,tbody,tr,td,th,figure,hr,details,summary,pre';
const MAX_TEXTS = 10;      // server limits (routes/translate.py)
const MAX_TEXT_LEN = 9000;
const ws = s => s.replace(/\s+/g, ' ');

const undo = new WeakMap();  // target element -> [restore fn, ...]

/** Runs of consecutive inline sibling nodes under `root` that hold some text. */
function runs(root) {
  const out = [];
  const walk = el => {
    let cur = [];
    const flush = () => { if (cur.some(hasText)) out.push(cur); cur = []; };
    for (const n of el.childNodes) {
      if (n.nodeType === Node.ELEMENT_NODE && !n.matches(ATOM) && (n.matches(BLOCK) || hasBlock(n))) { flush(); walk(n); }
      else if (n.nodeType === Node.TEXT_NODE || n.nodeType === Node.ELEMENT_NODE) cur.push(n);
    }
    flush();
  };
  walk(root);
  return out;
}

const hasText = n => n.nodeType === Node.TEXT_NODE ? !!n.data.trim() : !n.matches(ATOM) && !!n.textContent.trim();
const hasBlock = el => [...el.children].some(c => !c.matches(ATOM) && (c.matches(BLOCK) || hasBlock(c)));

/** Run -> { text, atoms, elems }: the run's text with inline elements as numbered tags. */
function encode(nodes) {
  const atoms = new Set(), elems = [];
  const enc = n => {
    if (n.nodeType === Node.TEXT_NODE) return ws(n.data);
    if (n.nodeType !== Node.ELEMENT_NODE) return '';
    const i = elems.push(n) - 1;
    if (n.matches(ATOM)) { atoms.add(i); return `<a i=${i}></a>`; }
    return `<a i=${i}>${[...n.childNodes].map(enc).join('')}</a>`;
  };
  return { text: nodes.map(enc).join('').trim(), atoms, elems };
}

/** Parses the translator's output back into a tree, reading only our own tags; anything else is
 *  text. Returns null if the tags are unbalanced, unknown or repeated. */
function parse(str, count) {
  const root = { kids: [] }, stack = [root], seen = new Set();
  const re = /<a i=(\d+)>|<\/a>/g;
  let last = 0, m;
  const text = s => { if (s) stack.at(-1).kids.push(s); };
  while ((m = re.exec(str))) {
    text(str.slice(last, m.index));
    last = re.lastIndex;
    if (m[1] === undefined) { if (stack.length < 2) return null; stack.pop(); continue; }
    const i = +m[1];
    if (i >= count || seen.has(i)) return null;
    seen.add(i);
    const node = { i, kids: [] };
    stack.at(-1).kids.push(node);
    stack.push(node);
  }
  text(str.slice(last));
  return stack.length === 1 ? { tree: root.kids, seen } : null;
}

/** Builds the translated nodes. Atoms are the original nodes, other elements shallow clones. */
function build(enc, translated) {
  const parsed = parse(translated, enc.elems.length);
  const kids = parsed ? parsed.tree : [translated.replace(/<a i=\d+>|<\/a>/g, '')];
  const make = k => {
    if (typeof k === 'string') return [document.createTextNode(k)];
    const orig = enc.elems[k.i];
    if (enc.atoms.has(k.i)) return [orig, ...k.kids.flatMap(make)];
    const el = orig.cloneNode(false);
    el.append(...k.kids.flatMap(make));
    return [el];
  };
  const out = kids.flatMap(make);
  // Media the translator dropped goes at the end rather than disappearing.
  for (const i of enc.atoms) if (!parsed?.seen.has(i) && !enc.elems[i].matches('br')) out.push(document.createTextNode(' '), enc.elems[i]);
  return out;
}

/** Swaps a run for its translation; returns the function that swaps it back. */
function apply(nodes, enc, translated) {
  const anchor = document.createTextNode('');
  nodes[0].before(anchor);
  nodes.forEach(n => n.remove());
  // Atoms nested inside the detached originals get a placeholder to return to; top-level ones
  // come back with `nodes`.
  const marks = [...enc.atoms].filter(i => enc.elems[i].parentNode)
    .map(i => { const c = document.createComment(''); enc.elems[i].replaceWith(c); return [c, enc.elems[i]]; });
  const fresh = build(enc, translated);
  anchor.after(...fresh);
  return () => {
    fresh.forEach(n => n.remove());
    anchor.after(...nodes);
    anchor.remove();
    marks.forEach(([c, atom]) => c.replaceWith(atom));
  };
}

async function post(texts) {
  const res = await fetch('/api/translate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ texts, to: escLang() }) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data.texts;
}

/** Translates single-line strings, sending them newline-joined in as few texts as fit; if the
 *  service merges or splits lines, falls back to one text per string. */
async function translateAll(strs) {
  const groups = [];
  let cur = [], len = 0;
  for (const s of strs) {
    if (cur.length && len + s.length + 1 > MAX_TEXT_LEN) { groups.push(cur); cur = []; len = 0; }
    cur.push(s); len += s.length + 1;
  }
  if (cur.length) groups.push(cur);
  const out = [];
  for (let i = 0; i < groups.length; i += MAX_TEXTS) {
    const batch = groups.slice(i, i + MAX_TEXTS);
    const res = await post(batch.map(g => g.join('\n')));
    for (let j = 0; j < batch.length; j++) {
      const lines = (res[j] || '').split('\n');
      if (lines.length === batch[j].length) { out.push(...lines); continue; }
      for (let k = 0; k < batch[j].length; k += MAX_TEXTS) out.push(...await post(batch[j].slice(k, k + MAX_TEXTS)));
    }
  }
  return out;
}

function targets(btn) {
  const comment = btn.closest('.comment');
  if (comment) return [comment.querySelector(':scope > .comment-body')];
  const root = btn.closest('#pv-content');
  return root ? [root.querySelector('.pv-title'), root.querySelector('.pv-body')] : [];
}

function restore(els, btn) {
  els.forEach(el => { (undo.get(el) || []).reverse().forEach(fn => fn()); undo.delete(el); });
  btn.textContent = 'translate';
}

async function toggle(btn) {
  const els = targets(btn).filter(Boolean);
  if (!els.length || btn.dataset.busy) return;
  if (els.some(el => undo.has(el))) { restore(els, btn); return; }
  btn.dataset.busy = '1';
  btn.textContent = 'translating…';
  try {
    const perEl = els.map(el => runs(el).map(nodes => ({ nodes, enc: encode(nodes) })));
    const all = perEl.flat();
    const texts = all.length ? await translateAll(all.map(r => r.enc.text)) : [];
    let i = 0;
    els.forEach((el, e) => undo.set(el, perEl[e].map(r => apply(r.nodes, r.enc, texts[i++] ?? r.enc.text))));
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
