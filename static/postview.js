import { state } from './state.js';
import { settings } from './settings.js';
import { escHtml, fmtNum, fmtDate, fmtDateTime, timeAgo, setActiveButton, renderFlair, renderAwards, errState } from './utils.js';
import { initMedia, initGifVideos, mediaHtmlFull } from './media.js';
import { renderCommentTree, renderMd, translatePost, renderCrosspostFull } from './render.js';

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
  // Gallery: downloads current image; gallery nav updates the href
  if (p.gallery?.length) {
    const first = p.gallery[0].url;
    if (!_pvDlOk(first)) return '';
    const base = first.split('?')[0].split('/').pop() || 'image';
    const dot = base.lastIndexOf('.');
    const fname = dot > 0 ? `${base.slice(0, dot)}-1${base.slice(dot)}` : `${base}-1`;
    return `<a class="share-btn pv-dl-gallery" href="${escHtml(`/api/download?url=${encodeURIComponent(first)}&filename=${encodeURIComponent(fname)}`)}" download="${escHtml(fname)}" title="Download current image">${_DL_SVG} download</a>`;
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
  const threadBanner = commentId ? `<div class="thread-banner"><a href="javascript:;" data-back="true">← View full thread</a></div>` : '';
  let rootComments = data.comments;
  if (commentId) {
    const target = findComment(data.comments, commentId);
    if (target) rootComments = [target];
  }
  if (!rootComments.length) return '<div class="state" style="padding:40px 0"><div class="state-icon">∅</div><div class="state-title">No comments yet</div></div>';
  return `<div class="pv-comments">${threadBanner}${renderCommentTree(rootComments, 0, p.subreddit, p.id, p.author)}</div>`;
}

// ── Exports ───────────────────────────────────────────────────────────────────
export function openPostView() {
  _pvPrevFocus = document.activeElement;
  postView.classList.add('open');
  document.body.style.overflow = 'hidden';
  const focusEl = document.getElementById('pv-home');
  if (focusEl) focusEl.focus();
}

export function closePostView() {
  postView.classList.remove('open');
  document.body.style.overflow = '';
  if (_pvPrevFocus) { _pvPrevFocus.focus(); _pvPrevFocus = null; }
}

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
  state._pvSub = sub; state._pvPostId = postId; state._pvCommentId = commentId;
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
      renderFlair(p, true),
    ].filter(Boolean).join('');
    const pvEditedHtml = p.edited_utc ? `<span class="edited-mark" title="edited ${fmtDate(p.edited_utc)}">*edited ${timeAgo(p.edited_utc)}</span>` : '';
    const bodyHtml = p.selftext?.trim() ? `<div class="pv-body md">${renderMd(p.selftext)}</div>` : '';
    const crosspostHtml = p.crosspost_from ? renderCrosspostFull(p.crosspost_from) : '';

    pvContent.innerHTML = `
      <a class="pv-sub-link" href="/r/${escHtml(p.subreddit)}" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>
      ${pvBadges ? `<div class="post-meta-top" style="margin-bottom:10px">${pvBadges}</div>` : ''}
      <h1 class="${titleClass}">${escHtml(p.title)}</h1>
      <div class="pv-meta">
        <span class="up">▲ ${fmtNum(p.score)}</span>
        <span>${p.upvote_ratio}% upvoted</span>
        <button class="meta-item link" data-user="${escHtml(p.author)}">u/${escHtml(p.author)}</button>
        <span title="${fmtDateTime(p.created_utc)}">${timeAgo(p.created_utc)}${pvEditedHtml ? ' '+pvEditedHtml : ''}</span>
        <span>${fmtNum(p.num_comments)} comments</span>
        ${!p.is_self && !p.crosspost_from ? `<a class="meta-item link" href="/r/${escHtml(p.subreddit)}/duplicates/${escHtml(p.id)}" data-nav="/r/${escHtml(p.subreddit)}/duplicates/${escHtml(p.id)}">duplicates</a>` : ''}
        <button class="share-btn" data-share="/r/${escHtml(p.subreddit)}/comments/${escHtml(p.id)}" title="Copy link">
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><circle cx="12" cy="3" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="12" cy="13" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="8" r="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M10.5 3.87 5.5 7.13M5.5 8.87l5 3.26" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
          share
        </button>
        ${buildDownloadBtn(p)}
        ${renderAwards(p.awards)}
      </div>
      ${crosspostHtml}
      ${p.crosspost_from ? '' : mediaHtmlFull(p)}
      ${!p.is_self && !p.crosspost_from && p.url && p.domain && !p.domain.startsWith('self.') && !p.domain.endsWith('redd.it') && !p.url.includes('reddit.com/gallery') ? `<a class="pv-article-link" href="${escHtml(p.url)}" target="_blank" rel="noopener"><svg width="13" height="13" viewBox="0 0 12 12" fill="none"><path d="M7 1h4m0 0v4m0-4L5.5 6.5M1 3h3.5M1 9h10M1 6h1.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>${escHtml(p.url)}</span></a>` : ''}
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
