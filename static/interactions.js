import { state } from './state.js';
import { openLightbox } from './lightbox.js';
import { cardContent } from './hibernate.js';
import { toggleSaved, saveBtnLabel } from './saved.js';
import { navigate, navigateOrOpen } from './nav.js';
import { retryFeedLoad } from './refresh.js';
import { loadSubFeed } from './feed.js';
import { loadPostView, changeCommentSort, loadMoreComments, stepViewFullThread } from './postview.js';

const feed      = document.getElementById('feed');
const pvContent = document.getElementById('pv-content');

// Header / bottom-nav shortcuts
document.getElementById('logo-btn').addEventListener('click', () => navigate('/'));
document.getElementById('bn-home').addEventListener('click', () => navigate('/'));
document.getElementById('bn-saved').addEventListener('click', () => navigate('/saved'));


// pv-home button
document.getElementById('pv-home').addEventListener('click', () => {
  navigate('/home');
});

// Comment collapse
document.getElementById('post-view').addEventListener('click', e => {
  const header = e.target.closest('.comment-header');
  if (!header || e.target.tagName==='A') return;
  const comment   = header.closest('.comment');
  const collapsed = comment.classList.toggle('collapsed');
  const btn = comment.querySelector(':scope > .comment-header > .comment-collapse');
  if (btn) btn.textContent = collapsed ? '+' : '−';
});

// pvContent: comment sort, load more, retry, user nav
pvContent.addEventListener('click', e => {
  if (handleFlairClick(e)) return;
  const threadNavBtn = e.target.closest('[data-thread-nav]');
  if (threadNavBtn) { e.preventDefault(); stepViewFullThread(); return; }
  const retryBtn = e.target.closest('.state-retry-btn[data-retry]');
  if (retryBtn) {
    const t = retryBtn.dataset.retry;
    if (t === 'post') loadPostView(state._pvSub, state._pvPostId, state._pvCommentId);
    else if (t === 'comments') changeCommentSort(state.currentCommentSort);
    return;
  }
  const csort = e.target.closest('[data-csort]');
  if (csort) { e.preventDefault(); changeCommentSort(csort.dataset.csort); return; }
  const moreBtn = e.target.closest('.load-more-btn');
  if (moreBtn) { e.preventDefault(); loadMoreComments(moreBtn); return; }
});

// Feed clicks: retry
feed.addEventListener('click', e => {
  if (e.defaultPrevented) return;
  const retryBtn = e.target.closest('.state-retry-btn[data-retry]');
  if (retryBtn) { retryFeedLoad(); return; }
  const continueBtn = e.target.closest('.state-continue-btn[data-sub]');
  if (continueBtn) {
    const { sub, sort, time } = continueBtn.dataset;
    loadSubFeed(sub, sort, time, null, false, true);
    return;
  }
});

// Spoiler reveal — delegated so it works in feed excerpts, post view body, and comments
document.addEventListener('click', e => {
  const spoiler = e.target.closest('.spoiler');
  if (!spoiler) return;
  if (e.target.closest('a')) return; // let a click on a revealed link's <a> navigate instead of re-hiding
  e.stopPropagation();
  spoiler.classList.toggle('revealed');
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const spoiler = e.target.closest('.spoiler');
  if (!spoiler) return;
  e.preventDefault();
  spoiler.classList.toggle('revealed');
});

// Flair / community / user card clicks
function handleFlairClick(e) {
  const flairEl = e.target.closest('.flair.flair-clickable[data-flair]');
  if (!flairEl) return false;
  e.stopPropagation();
  const sub   = flairEl.dataset.sub;
  const flair = flairEl.dataset.flair;
  if (sub && flair) {
    const query = 'flair:"'+flair+'"';
    const titleHtml = flairEl.outerHTML
      .replace(' flair-clickable', '')
      .replace(/ data-flair="[^"]*"/, '')
      .replace(/ data-sub="[^"]*"/, '');
    state.searchFlairNav = { query, html: titleHtml };
    navigateOrOpen(`/search?q=${encodeURIComponent(query)}&sub=${encodeURIComponent(sub)}&sort=new`, e);
  }
  return true;
}
feed.addEventListener('click', e => {
  if (handleFlairClick(e)) return;
  const card = e.target.closest('.community-card[data-nav]');
  if (card) { navigateOrOpen(card.dataset.nav, e); return; }
  const commentCard = e.target.closest('.user-comment-card[data-nav]');
  if (commentCard && !e.target.closest('a')) { navigateOrOpen(commentCard.dataset.nav, e); return; }
});
feed.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const card = e.target.closest('.community-card[data-nav]');
  if (card) { e.preventDefault(); navigateOrOpen(card.dataset.nav, e); return; }
  const commentCard = e.target.closest('.user-comment-card[data-nav]');
  if (commentCard) { e.preventDefault(); navigateOrOpen(commentCard.dataset.nav, e); }
});

// Save / unsave (local)
function _setSaveBtn(btn, saved) {
  btn.classList.toggle('is-saved', saved);
  btn.setAttribute('aria-pressed', String(saved));
  btn.title = saved ? 'Remove from saved' : 'Save post';
  btn.setAttribute('aria-label', btn.title);
  btn.innerHTML = saveBtnLabel(saved, btn.dataset.minimal === '1');
}

document.addEventListener('click', e => {
  const btn = e.target.closest('.save-btn[data-save]');
  if (!btn) return;
  e.stopPropagation();
  const id = btn.dataset.save;
  const saved = toggleSaved(id);
  if (saved === null) {
    btn.title = 'Could not save (storage full or unavailable)';
    return;
  }
  const sel = `.save-btn[data-save="${CSS.escape(id)}"]`;
  document.querySelectorAll(sel).forEach(b => _setSaveBtn(b, saved));
  // A hibernated feed card holds its buttons outside the document.
  const card = feed.querySelector(`[data-post-id="${CSS.escape(id)}"]`);
  if (card) cardContent(card).querySelectorAll(sel).forEach(b => _setSaveBtn(b, saved));
});

// Share / copy link
document.addEventListener('click', e => {
  const btn = e.target.closest('.share-btn[data-share]');
  if (!btn) return;
  e.stopPropagation();
  const url = 'https://www.reddit.com' + btn.dataset.share;
  const flash = () => {
    const prev = btn.innerHTML;
    btn.innerHTML = '✓ copied';
    btn.classList.add('share-copied');
    setTimeout(() => { btn.innerHTML = prev; btn.classList.remove('share-copied'); }, 1500);
  };
  if (navigator.clipboard) {
    navigator.clipboard.writeText(url).then(flash).catch(() => {});
  } else {
    const ta = Object.assign(document.createElement('textarea'), { value: url });
    ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    try { document.execCommand('copy'); flash(); } catch {}
    ta.remove();
  }
});


document.addEventListener('click', e => {
  const img = e.target.closest('.post-media img, .pv-media img, .md img, .gallery-main-img');
  if (!img) return;
  e.preventDefault();
  e.stopPropagation();
  openLightbox(img.dataset.full || img.src);
});

document.addEventListener('click', e => {
  if (e.target.closest('.spoiler-veil, .nsfw-veil')) return;
  const thumb = e.target.closest('.thumb-lightbox[data-lightbox]');
  if (!thumb) return;
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  e.stopPropagation();
  if (thumb.dataset.gallery) {
    try { openLightbox(JSON.parse(thumb.dataset.gallery), 0); return; } catch {}
  }
  openLightbox(thumb.dataset.lightbox);
});
