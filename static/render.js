import { escHtml, evictMap, fmtNum, fmtDate, fmtDateTime, timeAgo, setActiveButton, renderFlair, renderAwards, renderAuthorFlair, ANIM_DELAY_STEP, ANIM_DELAY_MAX } from './utils.js';
import { mediaHtmlCard, mediaHtmlFull, nsfwWrap } from './media.js';
import { isVisited } from './visited.js';
import { settings } from './settings.js';

const THREAD_MAX_DEPTH = 4;

// ── Markdown libs (marked + DOMPurify) — loaded on demand ────────────────────
let _mdLibsReady = false;
let _mdLibsPromise = null;

function _loadMdLibs() {
  if (_mdLibsReady || _mdLibsPromise) return _mdLibsPromise || Promise.resolve();
  let loaded = 0;
  _mdLibsPromise = new Promise(resolve => {
    for (const file of ['marked.min.js', 'purify.min.js']) {
      const s = document.createElement('script');
      s.src = `/static/${file}`;
      s.onload = () => { if (++loaded === 2) { _mdLibsReady = true; resolve(); } };
      document.head.appendChild(s);
    }
  });
  return _mdLibsPromise;
}
_loadMdLibs();

let _markedReady = false;
function _initMarked() {
  if (_markedReady) return;
  _markedReady = true;
  const r = new marked.Renderer();
  const _img  = r.image.bind(r);
  const _link = r.link.bind(r);
  r.image = (href, title, text) => {
    if (href?.startsWith('giphy|'))   return `<img src="https://media.giphy.com/media/${href.slice(6)}/giphy.gif" alt="${text||'gif'}" loading="lazy">`;
    if (href?.startsWith('redgifs|')) return `<div class="md-gif-embed redgifs-wrap" data-rgid="${href.slice(8)}"><div class="rg-loading"></div></div>`;
    if (href?.startsWith('redditvid|')) {
      const base = `https://v.redd.it/${href.slice(10)}`;
      return `<div class="md-video-embed post-video" data-hls="${base}/HLSPlaylist.m3u8" data-src="${base}/DASH_480.mp4" data-audio="${base}/DASH_audio.mp4"><video controls preload="metadata" playsinline muted></video></div>`;
    }
    try {
      const h = new URL(href).hostname;
      if (h === 'preview.redd.it' || h === 'external-preview.redd.it')
        href = `/api/img?url=${encodeURIComponent(href)}`;
    } catch (_) {}
    return _img(href, title, text);
  };
  r.link = (href, title, text) => {
    const decodedText = text ? text.replace(/&amp;/g, '&') : text;
    if (href && /\.(jpe?g|gif|png|webp|avif)(\?|$)/i.test(href) && (!decodedText || decodedText === href)) {
      const proxied = (href.includes('preview.redd.it') || href.includes('external-preview.redd.it'))
        ? `/api/img?url=${encodeURIComponent(href)}` : href;
      return `<a href="${proxied}" target="_blank" rel="noopener"><img src="${proxied}" alt="" loading="lazy"></a>`;
    }
    const base = _link(href, title, text) || '';
    // Relative links and reddit.com links are intercepted by the SPA router — no _blank
    if (!href || !/^https?:\/\//i.test(href) || /reddit\.com\//i.test(href))
      return base;
    return base.replace('<a ', '<a target="_blank" rel="noopener" ');
  };
  marked.use({ renderer: r, breaks: true, gfm: true });
}

// Comments often just paste reddit's video player URL as plain text (no real embed
// metadata is exposed for comments, unlike posts) — rewrite it into a video embed.
function embedRedditCommentVideos(text) {
  return text.replace(
    /(`[^`]*`|\[[^\]]*\]\([^)]*\))|https?:\/\/(?:www\.)?reddit\.com\/link\/[A-Za-z0-9_]+\/video\/([A-Za-z0-9_]+)\/?(?:player)?\/?/gi,
    (m, skip, vid) => skip ? skip : `![](redditvid|${vid})`);
}

export function linkifyReddit(text) {
  return text
    .replace(/(`[^`]*`|\[[^\]]*\]\([^\)]*\))|(?<![\w/])(\/?)r\/([A-Za-z0-9_]+(?:\/comments\/[A-Za-z0-9_]+)?)/g,
      (m, skip, slash, sub) => skip ? skip : `[r/${sub}](/r/${sub})`)
    .replace(/(`[^`]*`|\[[^\]]*\]\([^\)]*\))|(?<![\w/])(\/?)u\/([A-Za-z0-9_-]+)/g,
      (m, skip, slash, user) => skip ? skip : `[u/${user}](/user/${user})`);
}

const _xlateCache = new Map();
const XLATE_CACHE_MAX = 500;
export async function xlateText(text) {
  if (!text?.trim()) return null;
  const key = text.trim().slice(0, 1000);
  if (_xlateCache.has(key)) return _xlateCache.get(key);
  const r = await fetch(`/api/translate?text=${encodeURIComponent(key)}`);
  const d = await r.json();
  const detected = (d.matches || []).find(m => m['detected-language'])?.['detected-language'] || '';
  const result = { detected, translated: d.responseData?.translatedText || '' };
  evictMap(_xlateCache, XLATE_CACHE_MAX);
  _xlateCache.set(key, result);
  return result;
}

export function waitForMdLibs() { return _loadMdLibs(); }

export function renderMd(text) {
  if (!text) return '';
  if (!_mdLibsReady) return '';
  _initMarked();
  const processed = embedRedditCommentVideos(linkifyReddit(text)).replace(/>!([\s\S]*?)(?:!<|$)/g, (_, inner) =>
    `<span class="spoiler" role="button" tabindex="0">${inner}</span>`);
  return DOMPurify.sanitize(marked.parse(processed), { ADD_TAGS: ['span'], ADD_ATTR: ['class', 'tabindex', 'role'] });
}

export async function translatePost(p, container) {
  const titleEl = container.querySelector('.pv-title');
  if (!titleEl) return;
  const titleRes = await xlateText(p.title);
  if (!titleRes || !titleRes.detected || titleRes.detected.toLowerCase().startsWith('en')) return;
  if (!titleRes.translated || titleRes.translated === p.title) return;

  const origTitle = p.title;
  const origBody  = p.selftext || '';

  titleEl.textContent = titleRes.translated;

  const bodyEl = container.querySelector('.pv-body');
  let bodyRes = null;
  let mdTranslated = null;
  let mdOrig = null;
  if (bodyEl && origBody.trim()) {
    bodyRes = await xlateText(origBody);
    mdOrig = renderMd(origBody);
    if (bodyRes?.translated && bodyRes.translated !== origBody) {
      mdTranslated = renderMd(bodyRes.translated);
      bodyEl.innerHTML = mdTranslated;
    }
  }

  const bar = document.createElement('div');
  bar.className = 'xlate-bar';
  bar.innerHTML = `<span class="xlate-label">Translated from ${titleRes.detected}</span><button class="xlate-btn">View original</button>`;
  titleEl.after(bar);

  const xlateBtn = bar.querySelector('.xlate-btn');
  let showingTranslation = true;
  xlateBtn.addEventListener('click', () => {
    showingTranslation = !showingTranslation;
    if (showingTranslation) {
      titleEl.textContent = titleRes.translated;
      if (bodyEl && mdTranslated) bodyEl.innerHTML = mdTranslated;
      xlateBtn.textContent = 'View original';
    } else {
      titleEl.textContent = origTitle;
      if (bodyEl && mdOrig != null) bodyEl.innerHTML = mdOrig;
      xlateBtn.textContent = 'View translated';
    }
  });
}

// ── Crosspost embed ───────────────────────────────────────────────────────────
function renderCrosspostEmbed(orig, full=false) {
  const sub  = escHtml(orig.subreddit || '');
  const id   = escHtml(orig.id || '');
  const nav  = `/r/${sub}/comments/${id}`;
  const mediaHtml = orig.id ? (full ? mediaHtmlFull(orig) : mediaHtmlCard(orig)) : '';
  const excerptHtml = orig.selftext?.trim()
    ? `<div class="xp-excerpt md${full ? ' xp-excerpt-full' : ''}">${renderMd(orig.selftext)}</div>` : '';
  return `<div class="crosspost-embed${full ? ' crosspost-embed-full' : ''}">
    <div class="crosspost-embed-header">↪ crossposted from <a href="/r/${sub}" data-nav="/r/${sub}">r/${sub}</a></div>
    <a class="crosspost-embed-title" href="${escHtml(nav)}" data-nav="${escHtml(nav)}">${escHtml(orig.title || '')}</a>
    ${mediaHtml}${excerptHtml}
  </div>`;
}

export function renderCrosspostFull(orig) { return renderCrosspostEmbed(orig, true); }

// ── Compact mode row ─────────────────────────────────────────────────────────
function _compactThumbSrc(m) {
  return m.gallery?.[0]?.url ?? m.preview_img ?? m.thumb_url ?? null;
}

function _compactHasMedia(m) {
  const isImageDomain = m.domain && (m.domain === 'i.redd.it' || m.domain === 'i.imgur.com' || /^i\.\w/.test(m.domain));
  return !m.is_self && (m.is_video || m.youtube_id || m.tiktok_id || m.redgifs_id || m.imgur_album_id || m.streamable_id || m.embed_url || m.gif_url || m.gallery?.length > 0 || isImageDomain);
}

// In minimal mode only show thumbnails for posts whose content IS an image/gallery.
// Videos, embeds, and link posts skip the thumb entirely.
function _isNativeImage(m) {
  const isImageDomain = m.domain && (m.domain === 'i.redd.it' || m.domain === 'i.imgur.com' || /^i\.\w/.test(m.domain));
  return !!(m.gallery?.length > 0 || isImageDomain || (m.gif_url && !m.gif_is_video));
}

function _thumbSpoilerWrap(html) {
  return `<div class="spoiler-media-wrap spoiler-thumb-wrap"><div class="spoiler-veil" role="button" tabindex="0" onclick="event.preventDefault();this.parentElement.classList.add('revealed')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.parentElement.classList.add('revealed')}"><span class="spoiler-veil-label">spoiler</span></div><div class="spoiler-content">${html}</div></div>`;
}
function _thumbNsfwWrap(html) {
  return `<div class="nsfw-media-wrap nsfw-thumb-wrap"><div class="nsfw-veil" role="button" tabindex="0" onclick="event.preventDefault();this.parentElement.classList.add('revealed')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.parentElement.classList.add('revealed')}"><span class="nsfw-veil-label">nsfw</span></div><div class="nsfw-content">${html}</div></div>`;
}

function renderCompactRow(p, { sub, id, delay, visitedClass, nsfwAttr, metaTop, titleLink, footer }) {
  const mediaSrc = p.crosspost_from || p;
  const galleryCount = mediaSrc.gallery?.length > 1 ? mediaSrc.gallery.length : 0;
  const imgSrc = settings.layout === 'minimal' ? (_isNativeImage(mediaSrc) ? _compactThumbSrc(mediaSrc) : null) : _compactThumbSrc(mediaSrc);
  const postNav = `/r/${sub}/comments/${id}`;
  let thumbHtml = '';
  if (imgSrc) {
    const thumbInner = `<img src="${escHtml(imgSrc)}" loading="lazy" alt="" onerror="this.parentElement.remove()">`;
    let thumbContent = thumbInner;
    if (p.is_spoiler) thumbContent = _thumbSpoilerWrap(thumbContent);
    if (p.over_18) thumbContent = _thumbNsfwWrap(thumbContent);
    const galleryBadge = galleryCount ? `<span class="gallery-badge"><svg width="14" height="14" viewBox="0 0 16 16" fill="none"><rect x="4.5" y="4.5" width="9" height="9" rx="1.3" stroke="#fff" stroke-width="1.3"/><path d="M2.5 11.5v-7a2 2 0 0 1 2-2h7" stroke="#fff" stroke-width="1.3" stroke-linecap="round"/></svg>${galleryCount}</span>` : '';
    thumbHtml = _compactHasMedia(mediaSrc)
      ? `<a class="post-compact-thumb" href="${postNav}" data-nav="${postNav}">${thumbContent}${galleryBadge}</a>`
      : `<a class="post-compact-thumb" href="${escHtml(mediaSrc.url)}" target="_blank" rel="noopener">${thumbContent}</a>`;
  } else if (!mediaSrc.is_self && mediaSrc.url && /^https?:\/\//.test(mediaSrc.url) && settings.layout !== 'minimal') {
    thumbHtml = `<a class="post-compact-thumb og-placeholder" href="${escHtml(mediaSrc.url)}" target="_blank" rel="noopener" data-og-url="${escHtml(mediaSrc.url)}" data-og-nsfw="${p.over_18 ? '1' : ''}"></a>`;
  }
  return `
    <div class="post post-compact${visitedClass}"${nsfwAttr} data-post-id="${id}" style="animation-delay:${delay}ms">
      <div class="post-compact-left">
        <div class="post-header">
          ${metaTop}
          ${titleLink}
        </div>
        ${footer}
      </div>
      ${thumbHtml}
    </div>`;
}

// ── Minimal mode row (old-reddit style flat list) ─────────────────────────────
function renderMinimalRow(p, { sub, id, visitedClass, nsfwAttr, showSub }) {
  const mediaSrc = p.crosspost_from || p;
  const postNav  = `/r/${sub}/comments/${id}`;
  const author   = escHtml(p.author);

  let thumbHtml = '';
  if (_isNativeImage(mediaSrc)) {
    const imgSrc = _compactThumbSrc(mediaSrc);
    if (imgSrc) {
      const galleryCount = mediaSrc.gallery?.length > 1 ? mediaSrc.gallery.length : 0;
      let thumbContent = `<img src="${escHtml(imgSrc)}" loading="lazy" alt="" onerror="this.parentElement.remove()">`;
      if (p.is_spoiler) thumbContent = _thumbSpoilerWrap(thumbContent);
      if (p.over_18)    thumbContent = _thumbNsfwWrap(thumbContent);
      const galleryBadge = galleryCount ? `<span class="gallery-badge"><svg width="14" height="14" viewBox="0 0 16 16" fill="none"><rect x="4.5" y="4.5" width="9" height="9" rx="1.3" stroke="#fff" stroke-width="1.3"/><path d="M2.5 11.5v-7a2 2 0 0 1 2-2h7" stroke="#fff" stroke-width="1.3" stroke-linecap="round"/></svg>${galleryCount}</span>` : '';
      thumbHtml = `<a class="min-thumb" href="${postNav}" data-nav="${postNav}">${thumbContent}${galleryBadge}</a>`;
    }
  }

  let badges = '';
  if (p.is_stickied) badges += `<span class="badge badge-sticky">📌</span>`;
  if (p.over_18)     badges += `<span class="nsfw-tag">nsfw</span>`;
  if (p.is_spoiler)  badges += `<span class="badge badge-spoiler">spoiler</span>`;
  if (p.locked)      badges += `<span class="badge badge-locked">locked</span>`;
  if (p.is_oc)       badges += `<span class="badge badge-oc">oc</span>`;
  if (p.poll)        badges += `<span class="badge badge-poll">poll</span>`;
  const flairHtml  = renderFlair(p, true);
  const titleExtra = (p.is_self ? ' min-title-self' : '');
  const domainHtml = !p.is_self && p.domain && !p.domain.startsWith('self.') && !p.domain.endsWith('redd.it')
    ? `<span class="min-domain">(${escHtml(p.domain)})</span>` : '';
  const editedHtml = p.edited_utc ? ` <span class="edited-mark" title="edited ${fmtDate(p.edited_utc)}">*edited</span>` : '';
  const subLink    = showSub ? `<a class="min-sub" href="/r/${sub}" data-nav="/r/${sub}">r/${sub}</a> · ` : '';

  return `
    <div class="post post-minimal${visitedClass}"${nsfwAttr} data-post-id="${id}">
      <div class="min-score"><svg width="8" height="6" viewBox="0 0 10 7" fill="none"><path d="M5 1L9 6H1L5 1Z" fill="#ff6b35"/></svg>${fmtNum(p.score)}</div>
      <div class="min-body">
        <div class="min-title-row">${badges}<a class="min-title${titleExtra}" href="${postNav}" data-nav="${postNav}">${escHtml(p.title)}</a>${domainHtml ? ' '+domainHtml : ''}${flairHtml ? ' '+flairHtml : ''}</div>
        <div class="min-meta">${subLink}<button class="min-author" data-user="${author}">u/${author}</button> · <span title="${fmtDateTime(p.created_utc)}">${timeAgo(p.created_utc)}${editedHtml}</span>${renderAwards(p.awards)} · <a class="min-comments" href="${postNav}" data-nav="${postNav}">${fmtNum(p.num_comments)} comments</a> · <button class="share-btn" data-share="${postNav}" title="Copy link">share</button></div>
      </div>
      ${thumbHtml}
    </div>`;
}

// ── Post card ─────────────────────────────────────────────────────────────────
export function renderPost(p, idx, showSub=false) {
  const sub    = escHtml(p.subreddit);
  const author = escHtml(p.author);
  const id     = escHtml(p.id);
  const delay  = Math.min(idx*ANIM_DELAY_STEP, ANIM_DELAY_MAX);
  let tags = '';
  if (p.is_stickied) tags += `<span class="badge badge-sticky">📌 pinned</span>`;
  if (p.over_18)     tags += `<span class="nsfw-tag">nsfw</span>`;
  if (p.is_spoiler)  tags += `<span class="badge badge-spoiler">spoiler</span>`;
  if (p.locked)      tags += `<span class="badge badge-locked">locked</span>`;
  if (p.is_oc)       tags += `<span class="badge badge-oc">oc</span>`;
  if (p.poll)        tags += `<span class="badge badge-poll">poll</span>`;
  tags += renderFlair(p, true);
  const titleClass = 'post-title'+(p.is_self?' is-italic':'');
  const domainHtml = !p.is_self && p.domain && !p.domain.startsWith('self.') && !p.domain.endsWith('redd.it') ? `<a class="ext-link" href="${escHtml(p.url)}" target="_blank" rel="noopener"><svg width="9" height="9" viewBox="0 0 12 12" fill="none"><path d="M7 1h4m0 0v4m0-4L5.5 6.5M1 3h3.5M1 9h10M1 6h1.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>${escHtml(p.domain)}</a>` : '';
  const subHtml = showSub ? `<a class="post-sub-link" href="/r/${sub}" data-nav="/r/${sub}">r/${sub}</a>` : '';
  const metaTop = (subHtml || tags) ? `<div class="post-meta-top">${subHtml}${tags}</div>` : '';
  const titleLink = `<a class="${titleClass}" href="/r/${sub}/comments/${id}" data-nav="/r/${sub}/comments/${id}">${escHtml(p.title)}</a>`;
  const editedHtml = p.edited_utc ? `<span class="edited-mark" title="edited ${fmtDate(p.edited_utc)}">*edited</span>` : '';
  const footer = `
      <div class="post-footer">
        <div class="footer-left">
          <div class="score-block">
            <svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M6 1L9 5H3L6 1Z" fill="#ff6b35"/></svg>
            <span class="score-num">${fmtNum(p.score)}</span>
            <div class="ratio-bar"><div class="ratio-fill" style="width:${p.upvote_ratio}%"></div></div>
          </div>
          <button class="post-author" data-user="${author}">u/${author}</button>
          <span class="meta-item" title="${fmtDateTime(p.created_utc)}">${timeAgo(p.created_utc)}${editedHtml ? ' '+editedHtml : ''}</span>
          ${renderAwards(p.awards)}
        </div>
        <div class="footer-right">
          ${domainHtml}
          <a class="comments-link" href="/r/${sub}/comments/${id}" data-nav="/r/${sub}/comments/${id}">
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M14 8c0 3.314-2.686 6-6 6a6.03 6.03 0 0 1-2.83-.706L2 14l.706-3.17A6.03 6.03 0 0 1 2 8c0-3.314 2.686-6 6-6s6 2.686 6 6Z" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>
            ${fmtNum(p.num_comments)} comments
          </a>
          <button class="share-btn" data-share="/r/${sub}/comments/${id}" title="Copy link">
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><circle cx="12" cy="3" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="12" cy="13" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="8" r="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M10.5 3.87 5.5 7.13M5.5 8.87l5 3.26" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
          </button>
        </div>
      </div>`;

  const nsfwAttr = p.over_18 ? ' data-nsfw="1"' : '';
  const visitedClass = isVisited(p.id) ? ' post-visited' : '';

  if (settings.layout === 'minimal') {
    return renderMinimalRow(p, { sub, id, visitedClass, nsfwAttr, showSub });
  }

  if (settings.layout === 'compact') {
    return renderCompactRow(p, { sub, id, delay, visitedClass, nsfwAttr, metaTop, titleLink, footer });
  }

  if (p.crosspost_from) {
    return `
    <div class="post${visitedClass}"${nsfwAttr} data-post-id="${id}" style="animation-delay:${delay}ms">
      <div class="post-header">
        ${metaTop}
        ${titleLink}
      </div>
      ${renderCrosspostEmbed(p.crosspost_from)}
      ${footer}
    </div>`;
  }

  const isImageDomain = p.domain && (p.domain === 'i.redd.it' || p.domain === 'i.imgur.com' || /^i\.\w/.test(p.domain));
  const isCompact = !p.is_self && !p.is_video && !p.youtube_id && !p.tiktok_id && !p.redgifs_id && !p.imgur_album_id && !p.streamable_id && !p.embed_url && !p.gif_url && !(p.gallery?.length > 1) && !isImageDomain;
  if (isCompact) {
    const imgSrc = p.gallery?.[0]?.url ?? p.thumb_url ?? p.preview_img ?? null;
    let thumbHtml = '';
    if (imgSrc) {
      const thumbInner = `<img src="${escHtml(imgSrc)}" loading="lazy" alt="" onerror="this.parentElement.remove()">`;
      let thumbContent = thumbInner;
      if (p.is_spoiler) thumbContent = _thumbSpoilerWrap(thumbContent);
      if (p.over_18) thumbContent = _thumbNsfwWrap(thumbContent);
      thumbHtml = `<a class="post-compact-thumb" href="${escHtml(p.url)}" target="_blank" rel="noopener">${thumbContent}</a>`;
    } else if (p.url && /^https?:\/\//.test(p.url) && settings.layout !== 'minimal') {
      thumbHtml = `<a class="post-compact-thumb og-placeholder" href="${escHtml(p.url)}" target="_blank" rel="noopener" data-og-url="${escHtml(p.url)}" data-og-nsfw="${p.over_18 ? '1' : ''}"></a>`;
    }
    return `
    <div class="post post-compact${visitedClass}"${nsfwAttr} data-post-id="${id}" style="animation-delay:${delay}ms">
      <div class="post-compact-left">
        <div class="post-header">
          ${metaTop}
          ${titleLink}
        </div>
        ${footer}
      </div>
      ${thumbHtml}
    </div>`;
  }

  const excerptContent = p.selftext_html && _mdLibsReady
    ? DOMPurify.sanitize(p.selftext_html, { ADD_TAGS: ['span'], ADD_ATTR: ['class', 'tabindex', 'role'] })
    : p.selftext ? renderMd(p.selftext) : '';
  const excerptInner = excerptContent ? `<div class="post-excerpt"><div class="md">${excerptContent}</div></div>` : '';
  let excerptHtml = excerptInner;
  if (excerptContent && p.is_spoiler) excerptHtml = `<div class="spoiler-media-wrap"><div class="spoiler-veil" role="button" tabindex="0" onclick="event.preventDefault();this.parentElement.classList.add('revealed')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.parentElement.classList.add('revealed')}"><span class="spoiler-veil-label">spoiler — click to reveal</span></div><div class="spoiler-content">${excerptHtml}</div></div>`;
  if (excerptContent && p.over_18) excerptHtml = `<div class="nsfw-media-wrap nsfw-text-wrap"><div class="nsfw-veil" role="button" tabindex="0" onclick="event.preventDefault();this.parentElement.classList.add('revealed')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.parentElement.classList.add('revealed')}"><span class="nsfw-veil-label">nsfw</span></div><div class="nsfw-content">${excerptHtml}</div></div>`;
  return `
    <div class="post${visitedClass}"${nsfwAttr} data-post-id="${id}" style="animation-delay:${delay}ms">
      <div class="post-header">
        ${metaTop}
        ${titleLink}
      </div>
      ${mediaHtmlCard(p)}
      ${excerptHtml}
      ${footer}
    </div>`;
}

// ── Comment tree ─────────────────────────────────────────────────────────────
export function renderCommentTree(comments, depth=0, sub='', postId='', postAuthor='') {
  return comments.map(c => {
    if (c.kind === 'more') {
      if (!c.children?.length) return '';
      const ids = c.children.slice(0, 100).join(',');
      const label = c.count > 0 ? `Load ${c.count} more comment${c.count !== 1 ? 's' : ''}` : 'Load more comments';
      return `<div class="more-comments-wrap" data-depth="${depth}">
        <button class="load-more-btn" data-sub="${escHtml(sub)}" data-post="${escHtml(postId)}" data-ids="${escHtml(ids)}" data-depth="${depth}">${label}</button>
      </div>`;
    }

    const isDeleted = !c.body || c.body==='[deleted]' || c.body==='[removed]';
    const isAutoMod = c.author === 'AutoModerator';
    const isStickied = c.stickied;
    // Match 'bot' at end of name, at start, or adjacent to separators/_/digits.
    // Avoids false positives like "Robotics" (bot mid-word after alpha) — Scunthorpe problem.
    const isBotUser = c.author && /(?:^|[_\-\d])bot(?:[_\-\d]|$)|bot$/i.test(c.author);
    const startCollapsed = isAutoMod || isBotUser;
    const isOP    = postAuthor && postAuthor !== '[deleted]' && !isDeleted && c.author === postAuthor;
    const isMod   = c.distinguished === 'moderator';
    const isAdmin = c.distinguished === 'admin';
    const permalinkHref = `/r/${escHtml(sub)}/comments/${escHtml(postId)}/_/${escHtml(c.id)}`;

    let repliesHtml = '';
    if (c.replies?.length) {
      if (depth >= THREAD_MAX_DEPTH) {
        const href = `/r/${escHtml(sub)}/comments/${escHtml(postId)}/_/${escHtml(c.id)}`;
        repliesHtml = `<div class="comment-replies"><a class="continue-thread" href="${href}" data-nav="${href}">Continue thread →</a></div>`;
      } else {
        repliesHtml = `<div class="comment-replies">${renderCommentTree(c.replies, depth+1, sub, postId, postAuthor)}</div>`;
      }
    }

    return `<div class="comment${isDeleted?' comment-deleted':''}${startCollapsed?' collapsed':''}${isStickied?' comment-stickied':''}" data-depth="${depth}">
      <div class="comment-header">
        <button class="comment-collapse">${startCollapsed?'+':'−'}</button>
        <span class="comment-author${isMod?' is-mod':''}" data-user="${escHtml(c.author)}">${escHtml(c.author)}</span>
        ${isMod      ? '<span class="comment-mod">MOD</span>'        : ''}
        ${isAdmin    ? '<span class="comment-admin">ADMIN</span>'    : ''}
        ${isOP       ? '<span class="comment-op">OP</span>'         : ''}
        ${isStickied ? '<span class="badge badge-sticky">📌 stickied</span>' : ''}
        ${renderAuthorFlair(c)}
        <span class="comment-score">▲ ${fmtNum(c.score)}</span>
        <a class="comment-time" href="${permalinkHref}" data-nav="${permalinkHref}" title="${fmtDateTime(c.created_utc)}">${timeAgo(c.created_utc)}</a>${c.edited_utc ? ' <span class="edited-mark">*edited</span>' : ''}
        ${renderAwards(c.awards)}
      </div>
      <div class="comment-body md">${isDeleted?'<em>[deleted]</em>':renderMd(c.body)}</div>
      ${repliesHtml}
    </div>`;
  }).join('');
}

// ── User / community / user cards ────────────────────────────────────────────
export function renderUserCommentCard(c, idx) {
  const delay = Math.min(idx*ANIM_DELAY_STEP, ANIM_DELAY_MAX);
  const postPath = `/r/${escHtml(c.subreddit)}/comments/${escHtml(c.link_id)}`;
  const commentPath = `${postPath}/_/${escHtml(c.id)}`;
  return `<div class="user-comment-card" tabindex="0" role="button" data-nav="${commentPath}" style="animation-delay:${delay}ms">
    <div class="ucc-context">
      <span>in <a href="/r/${escHtml(c.subreddit)}" data-nav="/r/${escHtml(c.subreddit)}">r/${escHtml(c.subreddit)}</a></span>
      <span>·</span>
      <a href="${postPath}" data-nav="${postPath}" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(c.link_title)}</a>
    </div>
    <div class="ucc-body md">${renderMd(c.body)}</div>
    <div class="ucc-footer">
      <span class="ucc-score">▲ ${fmtNum(c.score)}</span>
      <span title="${fmtDateTime(c.created_utc)}">${timeAgo(c.created_utc)}</span>
    </div>
  </div>`;
}

export function renderCommunityCard(c, idx) {
  const delay = Math.min(idx*ANIM_DELAY_STEP, ANIM_DELAY_MAX);
  const letter = escHtml((c.name||'?')[0].toUpperCase());
  const iconHtml = c.icon
    ? `<img src="${escHtml(c.icon)}" alt="" onerror="this.outerHTML='<span>${letter}</span>'">`
    : `<span>${letter}</span>`;
  return `<div class="community-card" tabindex="0" role="button" style="animation-delay:${delay}ms" data-nav="/r/${escHtml(c.name)}">
    <div class="community-card-icon">${iconHtml}</div>
    <div class="community-card-body">
      <div class="community-card-name">r/${escHtml(c.name)}</div>
      ${c.title ? `<div class="community-card-title">${escHtml(c.title)}</div>` : ''}
      ${c.description ? `<div class="community-card-desc">${escHtml(c.description)}</div>` : ''}
      <div class="community-card-stats"><span>${fmtNum(c.subscribers||0)}</span> members${c.over_18 ? ' · <span style="color:#ff5050">nsfw</span>' : ''}</div>
    </div>
  </div>`;
}

export function renderUserCard(u, idx) {
  const delay = Math.min(idx*ANIM_DELAY_STEP, ANIM_DELAY_MAX);
  const letter = escHtml((u.name||'?')[0].toUpperCase());
  const iconHtml = u.icon
    ? `<img src="${escHtml(u.icon)}" alt="" onerror="this.outerHTML='<span>${letter}</span>'">`
    : `<span>${letter}</span>`;
  return `<div class="user-card" tabindex="0" role="button" style="animation-delay:${delay}ms" data-nav="/user/${escHtml(u.name)}">
    <div class="user-card-icon">${iconHtml}</div>
    <div class="user-card-body">
      <div class="user-card-name">u/${escHtml(u.name)}</div>
      <div class="user-card-stats">
        <span>${fmtNum(u.karma_post||0)}</span> post karma · <span>${fmtNum(u.karma_comment||0)}</span> comment karma
        ${u.created_utc ? ` · joined ${fmtDate(u.created_utc)}` : ''}
      </div>
    </div>
  </div>`;
}

export function renderLiveUpdate(u, isNew=false) {
  const body = u.body?.trim()
    ? `<div class="live-update-body md${u.stricken ? ' live-update-body-stricken' : ''}">${renderMd(u.body)}</div>`
    : '';
  return `<div class="live-update${u.stricken ? ' live-update-stricken' : ''}${isNew ? ' live-update-new' : ''}">
    <div class="live-update-meta">
      <span class="live-update-time" title="${new Date(u.created_utc * 1000).toISOString()}">${timeAgo(u.created_utc)}</span>
      <button class="live-update-author" data-user="${escHtml(u.author)}">u/${escHtml(u.author)}</button>
      ${u.stricken ? '<span class="live-update-retracted">retracted</span>' : ''}
    </div>
    ${body}
  </div>`;
}
