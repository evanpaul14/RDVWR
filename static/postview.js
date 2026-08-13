import { state } from './state.js';
import { settings } from './settings.js';
import { escHtml, fmtNum, fmtDate, fmtDateTime, timeAgo, setActiveButton, renderFlair, renderAwards, errState } from './utils.js';
import { initMedia, initGifVideos, mediaHtmlFull } from './media.js';
import { renderCommentTree, renderMd, translatePost, renderCrosspostFull, renderLinkedPostFull } from './render.js';

// ── Download button ───────────────────────────────────────────────────────────
const _PV_DL_HOSTS = new Set(['v.redd.it','i.redd.it','preview.redd.it','external-preview.redd.it','i.imgur.com']);
function _pvDlOk(url) {
  if (!url) return false;
  try { return _PV_DL_HOSTS.has(new URL(url).hostname); } catch { return false; }
}
function _pvDlExt(url) {
  const ext = url.split('?')[0].split('.').pop().toLowerCase();
  return ['jpg','jpeg','png','gif','webp'].includes(ext) ? ext : 'jpg';
}
const _DL_SVG = `<svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M8 2v8M5 7l3 3 3-3M3 13h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function buildDownloadBtn(p) {
  // Redgifs: placeholder replaced by initRedgifs once video URL is resolved
  if (p.redgifs_id) {
    return `<span class="share-btn pv-dl-placeholder" data-rg-dl="${escHtml(p.redgifs_id)}" title="Download (loading…)">${_DL_SVG} download</span>`;
  }
  // Gallery: opens selection popup
  if (p.gallery?.length) {
    if (!p.gallery.some(g => _pvDlOk(g.url))) return '';
    return `<button class="share-btn pv-gallery-dl-btn" title="Download images">${_DL_SVG} download</button>`;
  }
  // Imgur album: placeholder replaced by initImgurAlbums once images are loaded
  if (p.imgur_album_id) {
    return `<span class="share-btn pv-dl-placeholder" data-imgur-dl="${escHtml(p.imgur_album_id)}" title="Download (loading…)">${_DL_SVG} download</span>`;
  }
  let url = '', filename = '';
  if (p.is_video && p.video_url) {
    if (p.hls_url) {
      const href = `/api/download/reddit-video?hls=${encodeURIComponent(p.hls_url)}&filename=${encodeURIComponent(p.id + '.mp4')}`;
      return `<a class="share-btn" href="${escHtml(href)}" download="${escHtml(p.id + '.mp4')}" title="Download video">${_DL_SVG} download</a>`;
    }
    url = p.video_url; filename = `${p.id}.mp4`;
  } else if (p.gif_url) {
    url = p.gif_url; filename = `${p.id}.${p.gif_is_video ? 'mp4' : 'gif'}`;
  } else if (!p.youtube_id && !p.tiktok_id && !p.streamable_id && !p.embed_url && !p.is_self && p.preview_img) {
    const rawImg = p.preview_img.startsWith('/api/img?url=')
      ? decodeURIComponent(p.preview_img.slice('/api/img?url='.length))
      : p.preview_img;
    url = rawImg; filename = `${p.id}.${_pvDlExt(rawImg)}`;
  }
  if (!url || !_pvDlOk(url)) return '';
  const href = `/api/download?url=${encodeURIComponent(url)}&filename=${encodeURIComponent(filename)}`;
  return `<a class="share-btn" href="${escHtml(href)}" download="${escHtml(filename)}" title="Download media">${_DL_SVG} download</a>`;
}

// ── DOM refs ──────────────────────────────────────────────────────────────────
const postView    = document.getElementById('post-view');
const pvContent   = document.getElementById('pv-content');
const pvScroll    = document.getElementById('pv-scroll');
const pvOpen      = document.getElementById('pv-open');
const pvBreadcrumb = document.getElementById('pv-breadcrumb');

// ── Private state ─────────────────────────────────────────────────────────────
let _pvPrevFocus = null;

const COMMENT_SORTS = [
  {value:'confidence',    label:'Best'},
  {value:'top',           label:'Top'},
  {value:'new',           label:'New'},
  {value:'controversial', label:'Controversial'},
  {value:'old',           label:'Old'},
  {value:'qa',            label:'Q&A'},
];

// ── Private helpers ───────────────────────────────────────────────────────────
function findComment(comments, id) {
  for (const c of comments) {
    if (c.id === id) return c;
    if (c.replies?.length) {
      const found = findComment(c.replies, id);
      if (found) return found;
    }
  }
  return null;
}

function buildCommentSortBar(active) {
  return `<div class="comment-sort-bar">${COMMENT_SORTS.map(s =>
    `<button class="sort-btn${s.value===active?' active':''}" data-csort="${s.value}">${s.label}</button>`
  ).join('')}</div>`;
}

function buildCommentsHtml(data, commentId) {
  const p = data.post;
  const showContext = state._pvShowingContext;
  let threadBanner = '';
  if (commentId) {
    const isTopLevel = data.comments.some(c => c.id === commentId);
    const navType = (!showContext && !isTopLevel) ? 'context' : 'full';
    threadBanner = `<div class="thread-banner"><a href="javascript:;" data-thread-nav="${navType}">← View full thread</a></div>`;
  }
  let rootComments = data.comments;
  if (commentId && !showContext) {
    const target = findComment(data.comments, commentId);
    if (target) rootComments = [target];
  }
  if (!rootComments.length) return '<div class="state" style="padding:40px 0"><div class="state-icon">∅</div><div class="state-title">No comments yet</div></div>';
  return `<div class="pv-comments">${threadBanner}${renderCommentTree(rootComments, 0, p.subreddit, p.id, p.author)}</div>`;
}

// ── Exports ───────────────────────────────────────────────────────────────────
export function openPostView() {
  document.getElementById('feed')?.querySelectorAll('video').forEach(v => { if (!v.paused) v.pause(); });
  _pvPrevFocus = document.activeElement;
  postView.classList.add('open');
  document.body.style.overflow = 'hidden';
  const focusEl = document.getElementById('pv-home');
  if (focusEl) focusEl.focus();
}

export function closePostView() {
  postView.classList.remove('open');
  postView.style.transform = '';
  postView.style.transition = '';
  if (!document.getElementById('settings-panel')?.classList.contains('open')) {
    document.body.style.overflow = '';
  }
  if (_pvPrevFocus) { _pvPrevFocus.focus(); _pvPrevFocus = null; }
}

// ── Swipe-to-dismiss ──────────────────────────────────────────────────────────
let _pvSwipeStartY = 0;
let _pvSwipeArmed = false;
let _pvSwipeDy = 0;

postView.addEventListener('touchstart', e => {
  _pvSwipeArmed = false;
  _pvSwipeDy = 0;
  if (pvScroll.scrollTop > 8) return;
  if (e.target.closest('video, button, a, input, select, .gallery-stage')) return;
  _pvSwipeStartY = e.touches[0].clientY;
}, { passive: true });

postView.addEventListener('touchmove', e => {
  if (!_pvSwipeStartY) return;
  if (pvScroll.scrollTop > 8) { _pvSwipeStartY = 0; return; }
  const dy = e.touches[0].clientY - _pvSwipeStartY;
  if (dy <= 0) { _pvSwipeArmed = false; return; }
  _pvSwipeArmed = true;
  _pvSwipeDy = dy;
  e.preventDefault();
  postView.classList.add('pv-dragging');
  postView.style.transform = `translateY(${Math.round(dy * 0.55)}px)`;
}, { passive: false });

postView.addEventListener('touchend', () => {
  postView.classList.remove('pv-dragging');
  if (!_pvSwipeArmed) { _pvSwipeStartY = 0; return; }
  const dy = _pvSwipeDy;
  _pvSwipeArmed = false;
  _pvSwipeStartY = 0;
  _pvSwipeDy = 0;

  if (dy >= 130) {
    postView.style.transition = 'transform .2s ease-in';
    postView.style.transform = `translateY(${dy}px)`;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      postView.style.transform = 'translateY(105vh)';
    }));
    postView.addEventListener('transitionend', function handler() {
      postView.removeEventListener('transitionend', handler);
      closePostView();
      history.back();
    }, { once: true });
  } else {
    postView.style.transition = 'transform .25s cubic-bezier(.4,0,.2,1)';
    postView.style.transform = 'translateY(0)';
    setTimeout(() => { postView.style.transition = ''; postView.style.transform = ''; }, 280);
  }
}, { passive: true });

export async function changeCommentSort(sort) {
  state.currentCommentSort = sort;
  setActiveButton(pvContent, 'csort', sort);
  const area = pvContent.querySelector('.pv-comments-area');
  if (!area) return;
  area.innerHTML = '<div class="state" style="padding:30px 0"><div class="state-icon">⌗</div><div class="state-title">Loading…</div></div>';
  try {
    const apiUrl = `/api/r/${encodeURIComponent(state._pvSub)}/comments/${encodeURIComponent(state._pvPostId)}?sort=${sort}${state._pvCommentId ? `&comment=${encodeURIComponent(state._pvCommentId)}` : ''}`;
    const res  = await fetch(apiUrl);
    if (!res.ok) { area.innerHTML = errState('Failed to load comments', 'comments'); return; }
    const data = await res.json();
    state._pvData = data;
    area.innerHTML = buildCommentsHtml(data, state._pvCommentId);
  } catch {
    area.innerHTML = errState('Network error', 'comments');
  }
}

export async function loadPostView(sub, postId, commentId='', restorePvScroll=0) {
  state._pvSub = sub; state._pvPostId = postId; state._pvCommentId = commentId; state._pvShowingContext = false;
  state.currentCommentSort = settings.commentSort;
  pvContent.innerHTML = '<div class="pv-loader"></div>';
  document.dispatchEvent(new CustomEvent('pv-load'));
  pvScroll.scrollTop = 0;
  openPostView();

  pvBreadcrumb.innerHTML = `<a href="/r/${escHtml(sub)}" data-nav="/r/${escHtml(sub)}">r/${escHtml(sub)}</a>`;
  pvOpen.href = '#';

  try {
    const apiUrl = `/api/r/${encodeURIComponent(sub)}/comments/${encodeURIComponent(postId)}?sort=${state.currentCommentSort}` + (commentId ? `&comment=${encodeURIComponent(commentId)}` : '');
    const res  = await fetch(apiUrl);
    const data = await res.json();
    if (!res.ok) { pvContent.innerHTML = errState(escHtml(data.error||'Failed to load'), 'post'); return; }
    state._pvData = data;

    const p = data.post;
    pvOpen.href = p.permalink;
    pvBreadcrumb.innerHTML = `<a href="/r/${escHtml(p.subreddit)}" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>`;
    document.title = p.title + ' — RDVWR';

    const titleClass = 'pv-title'+(p.is_self?' is-italic':'');
    const pvBadges = [
      p.is_stickied ? '<span class="badge badge-sticky">📌 pinned</span>' : '',
      p.over_18     ? '<span class="nsfw-tag">nsfw</span>' : '',
      p.is_spoiler  ? '<span class="badge badge-spoiler">spoiler</span>' : '',
      p.locked      ? '<span class="badge badge-locked">locked</span>' : '',
      p.is_oc       ? '<span class="badge badge-oc">oc</span>' : '',
      renderFlair(p),
    ].filter(Boolean).join('');
    const pvEditedHtml = p.edited_utc ? `<span class="edited-mark" title="edited ${fmtDate(p.edited_utc)}">*edited ${timeAgo(p.edited_utc)}</span>` : '';
    const bodyInner = p.selftext?.trim() ? `<div class="pv-body md">${renderMd(p.selftext)}</div>` : '';
    const bodyHtml = (bodyInner && p.over_18)
      ? `<div class="nsfw-media-wrap nsfw-text-wrap"><div class="nsfw-veil" role="button" tabindex="0" onclick="event.preventDefault();this.parentElement.classList.add('revealed')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.parentElement.classList.add('revealed')}"><span class="nsfw-veil-label">nsfw</span></div><div class="nsfw-content">${bodyInner}</div></div>`
      : bodyInner;
    const crosspostHtml = p.crosspost_from ? renderCrosspostFull(p.crosspost_from) : (p.linked_post ? renderLinkedPostFull(p.linked_post) : '');

    pvContent.innerHTML = `
      <a class="pv-sub-link" href="/r/${escHtml(p.subreddit)}" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>
      ${pvBadges ? `<div class="post-meta-top" style="margin-bottom:10px">${pvBadges}</div>` : ''}
      <h1 class="${titleClass}">${escHtml(p.title)}</h1>
      ${settings.layout === 'minimal' ? `
      <div class="pv-meta">
        <span class="up">▲ ${fmtNum(p.score)}</span>
        <a class="meta-item link" href="/user/${escHtml(p.author)}" data-user="${escHtml(p.author)}" data-nav="/user/${escHtml(p.author)}">u/${escHtml(p.author)}</a>
        <span title="${fmtDateTime(p.created_utc)}">${timeAgo(p.created_utc)}${pvEditedHtml ? ' '+pvEditedHtml : ''}</span>
        <span>${fmtNum(p.num_comments)} comments</span>
        <button class="share-btn min-share" data-share="/r/${escHtml(p.subreddit)}/comments/${escHtml(p.id)}" title="Copy link">share</button>
        ${renderAwards(p.awards)}
      </div>` : `
      <div class="pv-meta">
        <span class="up">▲ ${fmtNum(p.score)}</span>
        <span>${p.upvote_ratio}% upvoted</span>
        <a class="meta-item link" href="/user/${escHtml(p.author)}" data-user="${escHtml(p.author)}" data-nav="/user/${escHtml(p.author)}">u/${escHtml(p.author)}</a>
        <span title="${fmtDateTime(p.created_utc)}">${timeAgo(p.created_utc)}${pvEditedHtml ? ' '+pvEditedHtml : ''}</span>
        <span>${fmtNum(p.num_comments)} comments</span>
        ${!p.is_self && !p.crosspost_from && !p.linked_post ? `<a class="meta-item link" href="/r/${escHtml(p.subreddit)}/duplicates/${escHtml(p.id)}" data-nav="/r/${escHtml(p.subreddit)}/duplicates/${escHtml(p.id)}">duplicates</a>` : ''}
        <button class="share-btn" data-share="/r/${escHtml(p.subreddit)}/comments/${escHtml(p.id)}" title="Copy link">
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><circle cx="12" cy="3" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="12" cy="13" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="8" r="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M10.5 3.87 5.5 7.13M5.5 8.87l5 3.26" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
          share
        </button>
        ${buildDownloadBtn(p)}
        ${renderAwards(p.awards)}
      </div>`}
      ${crosspostHtml}
      ${p.crosspost_from || p.linked_post ? '' : mediaHtmlFull(p)}
      ${settings.layout !== 'minimal' && !p.is_self && !p.crosspost_from && !p.linked_post && p.url && p.domain && !p.domain.startsWith('self.') && !p.domain.endsWith('redd.it') && !p.url.includes('reddit.com/gallery') && !p.is_video && !p.youtube_id && !p.tiktok_id && !p.redgifs_id && !p.streamable_id && !p.embed_url && !(p.gif_url && p.gif_is_video) ? `<div class="pv-article-box"><a class="pv-article-link" href="${escHtml(p.url)}" target="_blank" rel="noopener"><svg width="13" height="13" viewBox="0 0 12 12" fill="none"><path d="M7 1h4m0 0v4m0-4L5.5 6.5M1 3h3.5M1 9h10M1 6h1.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>${escHtml(p.url)}</span></a><div class="pv-article-desc" data-og-url="${escHtml(p.url)}"></div></div>` : ''}
      ${bodyHtml}
      <div class="pv-divider">
        <div class="pv-divider-line"></div>
      </div>
      ${buildCommentSortBar(state.currentCommentSort)}
      <div class="pv-comments-area">
        ${buildCommentsHtml(data, commentId)}
      </div>`;

    initMedia(pvContent);
    initGifVideos(pvContent);
    if (restorePvScroll) pvScroll.scrollTop = restorePvScroll;
    translatePost(p, pvContent).catch(() => {});
  } catch {
    pvContent.innerHTML = errState('Network error', 'post');
  }
}

export async function stepViewFullThread() {
  const area = pvContent.querySelector('.pv-comments-area');
  if (!area || !state._pvCommentId || !state._pvData) return;
  if (!state._pvShowingContext && !state._pvData.comments.some(c => c.id === state._pvCommentId)) {
    state._pvShowingContext = true;
    area.innerHTML = buildCommentsHtml(state._pvData, state._pvCommentId);
    initMedia(area);
    initGifVideos(area);
  } else {
    state._pvShowingContext = false;
    state._pvCommentId = '';
    await changeCommentSort(state.currentCommentSort);
  }
}

export async function loadMoreComments(btn) {
  const sub    = btn.dataset.sub;
  const postId = btn.dataset.post;
  const ids    = btn.dataset.ids;
  const depth  = parseInt(btn.dataset.depth, 10) || 0;
  const wrap   = btn.closest('.more-comments-wrap');
  if (!wrap) return;
  btn.disabled = true;
  btn.textContent = 'Loading…';
  try {
    const url = `/api/r/${encodeURIComponent(sub)}/morechildren/${encodeURIComponent(postId)}?children=${encodeURIComponent(ids)}&sort=${state.currentCommentSort}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (!res.ok) { btn.textContent = 'Failed to load'; btn.disabled = false; return; }
    if (!data.comments?.length) { wrap.remove(); return; }
    const html = renderCommentTree(data.comments, depth, sub, postId, state._pvData?.post?.author || '');
    wrap.insertAdjacentHTML('afterend', html);
    initMedia(wrap.parentElement);
    wrap.remove();
  } catch {
    btn.textContent = 'Failed to load';
    btn.disabled = false;
  }
}

// ── Gallery download modal ────────────────────────────────────────────────────

function _showGalleryDlModal(gallery, postId) {
  document.getElementById('gallery-dl-modal')?.remove();

  const eligible = gallery.filter(g => _pvDlOk(g.url));
  if (!eligible.length) return;

  const itemsHtml = eligible.map((img, i) => `
    <label class="gdl-item">
      <input type="checkbox" class="gdl-check" checked data-url="${escHtml(img.url)}">
      <div class="gdl-thumb"><img src="${escHtml(img.url)}" alt="${escHtml(img.caption || '')}" loading="lazy"></div>
      ${img.caption ? `<span class="gdl-caption">${escHtml(img.caption)}</span>` : `<span class="gdl-caption">${i + 1}</span>`}
    </label>`).join('');

  const modal = document.createElement('div');
  modal.id = 'gallery-dl-modal';
  modal.innerHTML = `
    <div class="gdl-box">
      <div class="gdl-header">
        <span class="gdl-title">Download images</span>
        <button class="gdl-close" aria-label="Close">×</button>
      </div>
      <div class="gdl-grid">${itemsHtml}</div>
      <div class="gdl-footer">
        <button class="gdl-sel-toggle">Unselect all</button>
        <span class="gdl-count">${eligible.length} / ${eligible.length}</span>
        <button class="gdl-download-btn">Download</button>
      </div>
    </div>`;

  const toggleBtn = modal.querySelector('.gdl-sel-toggle');
  const countEl   = modal.querySelector('.gdl-count');

  function _updateState() {
    const checks = [...modal.querySelectorAll('.gdl-check')];
    const n = checks.filter(c => c.checked).length;
    const dlBtn = modal.querySelector('.gdl-download-btn');
    dlBtn.disabled = n === 0;
    countEl.textContent = `${n} / ${checks.length}`;
    toggleBtn.textContent = n === checks.length ? 'Unselect all' : 'Select all';
  }

  modal.addEventListener('change', e => { if (e.target.classList.contains('gdl-check')) _updateState(); });
  modal.querySelector('.gdl-close').addEventListener('click', () => modal.remove());
  toggleBtn.addEventListener('click', () => {
    const checks = [...modal.querySelectorAll('.gdl-check')];
    const allChecked = checks.every(c => c.checked);
    checks.forEach(c => c.checked = !allChecked);
    _updateState();
  });
  modal.querySelector('.gdl-download-btn').addEventListener('click', () => {
    const urls = [...modal.querySelectorAll('.gdl-check:checked')].map(c => c.dataset.url).join(',');
    if (!urls) return;
    window.location.href = `/api/download/gallery?urls=${encodeURIComponent(urls)}&name=${encodeURIComponent(postId)}`;
  });
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });

  document.body.appendChild(modal);
}

document.addEventListener('click', e => {
  const btn = e.target.closest('.pv-gallery-dl-btn');
  if (!btn) return;
  const gallery = state._pvData?.post?.gallery;
  if (!gallery?.length) return;
  const dlName = state._pvData?.post?.title || state._pvPostId || 'gallery';
  _showGalleryDlModal(gallery, dlName);
});

pvOpen.addEventListener('click', e => {
  const href = pvOpen.getAttribute('href');
  if (!href || href === '#') { e.preventDefault(); return; }
  e.preventDefault();
  window.open(href, '_blank', 'noopener');
});
