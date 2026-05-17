// ═══════════════════════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════════════════════
function fmtNum(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return String(n);
}
function timeAgo(utc) {
  const s = Math.floor(Date.now()/1000) - utc;
  if (s < 60)      return `${s}s`;
  if (s < 3600)    return `${Math.floor(s/60)}m`;
  if (s < 86400)   return `${Math.floor(s/3600)}h`;
  if (s < 2592000) return `${Math.floor(s/86400)}d`;
  if (s < 31536000)return `${Math.floor(s/2592000)}mo`;
  return `${Math.floor(s/31536000)}y`;
}
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function fmtDate(utc) {
  return new Date(utc*1000).toLocaleDateString(undefined, {year:'numeric',month:'short',day:'numeric'});
}

let userPrefersMuted = localStorage.getItem('mutePreference') !== 'unmuted';

const SKELETON_COUNT       = 5;
const ANIM_DELAY_STEP      = 40;
const ANIM_DELAY_MAX       = 400;
const AUTOCOMPLETE_DEBOUNCE = 280;
const TOUCH_MOVE_THRESHOLD  = 10;
const GALLERY_SWIPE_MIN     = 40;

// ═══════════════════════════════════════════════════════════════════════════
// MARKDOWN
// ═══════════════════════════════════════════════════════════════════════════
const mdRenderer = new marked.Renderer();
const _img  = mdRenderer.image.bind(mdRenderer);
const _link = mdRenderer.link.bind(mdRenderer);
mdRenderer.image = (href, title, text) => {
  if (href?.startsWith('giphy|'))   return `<img src="https://media.giphy.com/media/${href.slice(6)}/giphy.gif" alt="${text||'gif'}" loading="lazy">`;
  if (href?.startsWith('redgifs|')) return `<div class="md-gif-embed redgifs-wrap" data-rgid="${href.slice(8)}"><div class="rg-loading"></div></div>`;
  return _img(href, title, text);
};
mdRenderer.link = (href, title, text) => {
  if (href && /\.(jpe?g|gif|png|webp|avif)(\?|$)/i.test(href))
    return `<a href="${href}" target="_blank" rel="noopener"><img src="${href}" alt="${text||''}" loading="lazy"></a>`;
  return (_link(href, title, text)||'').replace('<a ', '<a target="_blank" rel="noopener" ');
};
marked.use({ renderer: mdRenderer, breaks: true, gfm: true });
function linkifyReddit(text) {
  // Convert bare r/sub and u/user mentions to markdown links,
  // but skip ones already inside a markdown link [...](...) or code spans `...`
  return text
    .replace(/(`[^`]*`|\[[^\]]*\]\([^\)]*\))|(?<![\/\w])r\/([A-Za-z0-9_]+)/g,
      (m, skip, sub) => skip ? skip : `[r/${sub}](/r/${sub})`)
    .replace(/(`[^`]*`|\[[^\]]*\]\([^\)]*\))|(?<![\/\w])u\/([A-Za-z0-9_-]+)/g,
      (m, skip, user) => skip ? skip : `[u/${user}](/user/${user})`);
}
function renderMd(text) {
  if (!text) return '';
  const processed = linkifyReddit(text).replace(/>!([\s\S]*?)!</g, (_, inner) =>
    `<span class="spoiler" onclick="this.classList.toggle('revealed')">${inner}</span>`);
  return DOMPurify.sanitize(marked.parse(processed), { ADD_TAGS: ['span'], ADD_ATTR: ['onclick', 'class'] });
}

function setActiveButton(container, dataAttr, activeVal) {
  container.querySelectorAll(`[data-${dataAttr}]`).forEach(b =>
    b.classList.toggle('active', b.dataset[dataAttr] === activeVal)
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// HLS VIDEO
// ═══════════════════════════════════════════════════════════════════════════
function syncAudio(videoEl, audioSrc) {
  const audio = new Audio(audioSrc);
  audio.preload = 'none';
  videoEl.addEventListener('play',         () => { audio.currentTime = videoEl.currentTime; audio.play().catch(()=>{}); });
  videoEl.addEventListener('pause',        () => audio.pause());
  videoEl.addEventListener('seeked',       () => { audio.currentTime = videoEl.currentTime; });
  videoEl.addEventListener('volumechange', () => { audio.volume = videoEl.volume; audio.muted = videoEl.muted; });
}
function setupHls(videoEl, hlsUrl, fallback, audioSrc) {
  if (hlsUrl && typeof Hls !== 'undefined' && Hls.isSupported()) {
    const hls = new Hls({ autoStartLoad: false });
    hls.loadSource(hlsUrl); hls.attachMedia(videoEl);
    videoEl.addEventListener('play', () => hls.startLoad(), { once: true });
  } else if (hlsUrl && videoEl.canPlayType('application/vnd.apple.mpegurl')) {
    videoEl.src = hlsUrl;
  } else if (fallback) {
    videoEl.src = fallback;
    if (audioSrc) syncAudio(videoEl, audioSrc);
  }
}
function initVideos(container) {
  container.querySelectorAll('[data-hls]:not([data-hls-init])').forEach(wrap => {
    const v = wrap.querySelector('video');
    if (v) {
      setupHls(v, wrap.dataset.hls, wrap.dataset.src, wrap.dataset.audio);
      if (wrap.dataset.poster) {
        const img = new Image();
        img.onload = () => { v.poster = wrap.dataset.poster; };
        img.src = wrap.dataset.poster;
      }
      wrap.dataset.hlsInit = '1';
    }
  });
  if (!userPrefersMuted) container.querySelectorAll('video').forEach(v => { v.muted = false; });
}

async function initRedgifs(container) {
  const wraps = [...container.querySelectorAll('.redgifs-wrap[data-rgid]:not([data-rg-init])')];
  await Promise.all(wraps.map(async wrap => {
    wrap.dataset.rgInit = '1';
    const id = wrap.dataset.rgid;
    try {
      const res = await fetch(`/api/redgifs/${id}`);
      const data = await res.json();
      if (!res.ok || (!data.hd && !data.sd)) throw new Error(data.error || 'no url');
      wrap.innerHTML = `<video controls playsinline preload="metadata" muted src="${escHtml(data.hd || data.sd)}"></video>`;
      if (!userPrefersMuted) { const v = wrap.querySelector('video'); if (v) v.muted = false; }
    } catch {
      wrap.innerHTML = `<div class="rg-error">Could not load video</div>`;
    }
  }));
}

// Returns true if a hex color is dark enough to use as a background in our dark theme.
// Filters out Reddit's near-white defaults like #edeff1.
function isUsableBg(hex) {
  if (!hex || hex === 'transparent') return false;
  const m = hex.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (!m) return false;
  const lum = 0.299*parseInt(m[1],16) + 0.587*parseInt(m[2],16) + 0.114*parseInt(m[3],16);
  return lum < 180;
}

function renderFlair(p, clickable=false) {
  if (!p.flair && !p.flair_richtext?.length) return '';
  let inner = '';
  if (p.flair_type === 'richtext' && p.flair_richtext?.length) {
    inner = p.flair_richtext.map(part => {
      if (part.e === 'text')  return escHtml(part.t || '');
      if (part.e === 'emoji') return `<img class="flair-emoji" src="${escHtml(part.u)}" alt="${escHtml(part.a||'')}" loading="lazy">`;
      return '';
    }).join('');
  } else {
    inner = escHtml(p.flair);
  }
  const bg = isUsableBg(p.flair_bg) ? p.flair_bg : '';
  const style = bg ? ` style="background:${escHtml(bg)};color:${p.flair_tc==='light'?'#fff':'#1a1a1a'}"` : '';
  const cls = clickable && p.flair ? ' flair-clickable' : '';
  const dataAttr = clickable && p.flair ? ` data-flair="${escHtml(p.flair)}" data-sub="${escHtml(p.subreddit)}"` : '';
  return `<span class="flair${cls}"${style}${dataAttr}>${inner}</span>`;
}

function renderAuthorFlair(c) {
  const hasRichtext = c.author_flair_type === 'richtext' && c.author_flair_richtext?.length;
  if (!hasRichtext && !c.author_flair_text) return '';
  let inner = '';
  if (hasRichtext) {
    inner = c.author_flair_richtext.map(part => {
      if (part.e === 'text')  return escHtml(part.t || '');
      if (part.e === 'emoji') return `<img class="author-flair-emoji" src="${escHtml(part.u)}" alt="${escHtml(part.a||'')}" loading="lazy">`;
      return '';
    }).join('');
  } else {
    inner = escHtml(c.author_flair_text);
  }
  if (!inner.trim() && !c.author_flair_richtext?.some(p => p.e === 'emoji')) return '';
  const bg = isUsableBg(c.author_flair_bg) ? c.author_flair_bg : '';
  const style = bg ? ` style="background:${escHtml(bg)};color:${c.author_flair_tc==='light'?'#fff':'#1a1a1a'}"` : '';
  return `<span class="author-flair"${style}>${inner}</span>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// GALLERY
// ═══════════════════════════════════════════════════════════════════════════
function renderGallery(images) {
  if (!images?.length) return '';
  const thumbsHtml = images.map((img,i) =>
    `<img class="gallery-thumb${i===0?' active':''}" src="${escHtml(img.url)}" data-idx="${i}" data-caption="${escHtml(img.caption||'')}" loading="lazy" alt="${escHtml(img.caption||'')}">`
  ).join('');
  return `
    <div class="gallery">
      <div class="gallery-stage">
        <img class="gallery-main-img" src="${escHtml(images[0].url)}" alt="${escHtml(images[0].caption||'')}">
        ${images.length > 1 ? `
          <div class="gallery-nav">
            <button class="gallery-btn gallery-prev" disabled>‹</button>
            <span class="gallery-counter">1 / ${images.length}</span>
            <button class="gallery-btn gallery-next">›</button>
          </div>` : ''}
      </div>
      ${images[0].caption ? `<div class="gallery-caption">${escHtml(images[0].caption)}</div>` : ''}
      ${images.length > 1 ? `<div class="gallery-thumbs">${thumbsHtml}</div>` : ''}
    </div>`;
}

// Delegate gallery navigation globally
document.addEventListener('click', e => {
  const prev  = e.target.closest('.gallery-prev');
  const next  = e.target.closest('.gallery-next');
  const thumb = e.target.closest('.gallery-thumb');
  const target = prev || next || thumb;
  if (!target) return;
  e.stopPropagation();

  const gallery  = target.closest('.gallery');
  const thumbs   = [...gallery.querySelectorAll('.gallery-thumb')];
  const mainImg  = gallery.querySelector('.gallery-main-img');
  const counter  = gallery.querySelector('.gallery-counter');
  const prevBtn  = gallery.querySelector('.gallery-prev');
  const nextBtn  = gallery.querySelector('.gallery-next');
  const caption  = gallery.querySelector('.gallery-caption');
  let cur = thumbs.findIndex(t => t.classList.contains('active'));
  if (cur === -1) cur = 0;

  let idx = cur;
  if (prev)  idx = Math.max(0, cur - 1);
  if (next)  idx = Math.min(thumbs.length - 1, cur + 1);
  if (thumb) idx = parseInt(thumb.dataset.idx);

  const t = thumbs[idx];
  mainImg.src = t.src; mainImg.alt = t.alt;
  if (counter) counter.textContent = `${idx+1} / ${thumbs.length}`;
  if (prevBtn) prevBtn.disabled = idx === 0;
  if (nextBtn) nextBtn.disabled = idx === thumbs.length - 1;
  if (caption) { caption.textContent = t.dataset.caption; caption.style.display = t.dataset.caption ? '' : 'none'; }
  thumbs.forEach((t,i) => t.classList.toggle('active', i === idx));
});

// Gallery swipe navigation
let _galleryTouchX = 0;
document.addEventListener('touchstart', e => {
  if (e.target.closest('.gallery-stage')) _galleryTouchX = e.touches[0].clientX;
}, { passive: true });
document.addEventListener('touchend', e => {
  const stage = e.target.closest('.gallery-stage');
  if (!stage || _galleryTouchX === 0) return;
  const dx = e.changedTouches[0].clientX - _galleryTouchX;
  _galleryTouchX = 0;
  if (Math.abs(dx) < GALLERY_SWIPE_MIN) return;
  const btn = stage.querySelector(dx < 0 ? '.gallery-next' : '.gallery-prev');
  if (btn && !btn.disabled) btn.click();
}, { passive: true });

// ═══════════════════════════════════════════════════════════════════════════
// POLL RENDERING
// ═══════════════════════════════════════════════════════════════════════════
function renderPoll(poll) {
  if (!poll?.options?.length) return '';
  const total = poll.total_votes || 0;
  const status = poll.closed ? 'Poll closed' : 'Poll open';
  const optionsHtml = poll.options.map(opt => {
    const count = opt.vote_count ?? null;
    const pct = (count !== null && total > 0) ? Math.round(count / total * 100) : null;
    const barHtml = pct !== null
      ? `<div class="poll-bar"><div class="poll-bar-fill" style="width:${pct}%"></div></div><span class="poll-pct">${pct}%</span>`
      : `<div class="poll-bar poll-bar-hidden"></div>`;
    return `<div class="poll-option">
      <span class="poll-option-text">${escHtml(opt.text)}</span>
      ${barHtml}
    </div>`;
  }).join('');
  return `<div class="poll-widget">
    <div class="poll-options">${optionsHtml}</div>
    <div class="poll-meta">
      <span class="poll-status${poll.closed ? ' poll-closed' : ' poll-open'}">${status}</span>
      <span class="poll-total">${fmtNum(total)} vote${total !== 1 ? 's' : ''}</span>
    </div>
  </div>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// MEDIA RENDERING
// ═══════════════════════════════════════════════════════════════════════════
function mediaHtmlCard(p) {
  if (p.poll) return renderPoll(p.poll);
  if (p.is_video) return `<div class="post-video" data-hls="${escHtml(p.hls_url||'')}" data-src="${escHtml(p.video_url||'')}" data-audio="${escHtml(p.audio_url||'')}"`+(p.preview_img?` data-poster="${escHtml(p.preview_img)}"`:'')+`><video controls preload="none" playsinline muted></video></div>`;
  if (p.youtube_id) return `<div class="post-video"><iframe src="https://www.youtube-nocookie.com/embed/${escHtml(p.youtube_id)}" allowfullscreen loading="lazy"></iframe></div>`;
  if (p.redgifs_id) return `<div class="post-video redgifs-wrap" data-rgid="${escHtml(p.redgifs_id)}"><div class="rg-loading"></div></div>`;
  if (p.embed_url)  return `<div class="post-video"><iframe src="${escHtml(p.embed_url)}" allowfullscreen loading="lazy" scrolling="no"></iframe></div>`;
  if (p.gif_url) return p.gif_is_video
    ? `<div class="post-video"><video src="${escHtml(p.gif_url)}" controls autoplay loop muted playsinline></video></div>`
    : `<div class="post-media"><img src="${escHtml(p.gif_url)}" loading="lazy" alt="" onerror="this.parentElement.classList.add('no-media')"></div>`;

  if (p.gallery?.length > 1) return renderGallery(p.gallery);
  const imgSrc = p.gallery?.length ? p.gallery[0].url : (!p.is_self ? p.preview_img : null);
  if (imgSrc) return `<div class="post-media">
    <img src="${escHtml(imgSrc)}" loading="lazy" alt="" onerror="this.parentElement.classList.add('no-media')">
  </div>`;
  if (!p.is_self) return `<div class="post-media link-thumb"><svg width="32" height="32" viewBox="0 0 24 24" fill="none"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>`;
  return '';
}

function mediaHtmlFull(p) {
  if (p.poll) return renderPoll(p.poll);
  if (p.is_video) return `<div class="pv-media" data-hls="${escHtml(p.hls_url||'')}" data-src="${escHtml(p.video_url||'')}" data-audio="${escHtml(p.audio_url||'')}"`+(p.preview_img?` data-poster="${escHtml(p.preview_img)}"`:'')+`><video controls preload="metadata" playsinline muted></video></div>`;
  if (p.youtube_id) return `<div class="pv-media"><iframe src="https://www.youtube-nocookie.com/embed/${escHtml(p.youtube_id)}" allowfullscreen loading="lazy"></iframe></div>`;
  if (p.redgifs_id) return `<div class="pv-media redgifs-wrap" data-rgid="${escHtml(p.redgifs_id)}"><div class="rg-loading"></div></div>`;
  if (p.embed_url)  return `<div class="pv-media"><iframe src="${escHtml(p.embed_url)}" allowfullscreen loading="lazy" scrolling="no"></iframe></div>`;
  if (p.gif_url) return p.gif_is_video
    ? `<div class="pv-media"><video src="${escHtml(p.gif_url)}" controls autoplay loop muted playsinline></video></div>`
    : `<div class="pv-media"><img src="${escHtml(p.gif_url)}" alt="" loading="lazy"></div>`;
  if (p.gallery?.length) return renderGallery(p.gallery);
  if (p.preview_img && !p.is_self) return `<div class="pv-media"><img src="${escHtml(p.preview_img)}" alt="" loading="lazy"></div>`;
  return '';
}

// ═══════════════════════════════════════════════════════════════════════════
// POST CARD RENDERING
// ═══════════════════════════════════════════════════════════════════════════
function renderPost(p, idx, showSub=false) {
  const sub   = escHtml(p.subreddit);
  const author = escHtml(p.author);
  const id    = escHtml(p.id);
  const delay = Math.min(idx*ANIM_DELAY_STEP, ANIM_DELAY_MAX);
  let tags = '';
  if (p.is_stickied) tags += `<span class="badge badge-sticky">📌 pinned</span>`;
  if (p.over_18)     tags += `<span class="nsfw-tag">nsfw</span>`;
  if (p.is_spoiler)  tags += `<span class="badge badge-spoiler">spoiler</span>`;
  if (p.locked)      tags += `<span class="badge badge-locked">locked</span>`;
  if (p.is_oc)       tags += `<span class="badge badge-oc">oc</span>`;
  tags += renderFlair(p, true);
  const titleClass = 'post-title'+(p.is_self?' is-italic':'');
  const domainHtml  = !p.is_self && p.domain ? `<a class="ext-link" href="${escHtml(p.url)}" target="_blank" rel="noopener"><svg width="9" height="9" viewBox="0 0 12 12" fill="none"><path d="M7 1h4m0 0v4m0-4L5.5 6.5M1 3h3.5M1 9h10M1 6h1.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>${escHtml(p.domain)}</a>` : '';
  const subHtml = showSub ? `<a class="post-sub-link" href="javascript:;" data-nav="/r/${sub}">r/${sub}</a>` : '';
  const metaTop = (subHtml || tags) ? `<div class="post-meta-top">${subHtml}${tags}</div>` : '';
  const titleLink = `<a class="${titleClass}" href="javascript:;" data-nav="/r/${sub}/comments/${id}">${escHtml(p.title)}</a>`;
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
          <span class="meta-item">${timeAgo(p.created_utc)}${editedHtml ? ' '+editedHtml : ''}</span>
        </div>
        <div class="footer-right">
          ${domainHtml}
          <button class="comments-link" data-sub="${sub}" data-id="${id}">
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M14 8c0 3.314-2.686 6-6 6a6.03 6.03 0 0 1-2.83-.706L2 14l.706-3.17A6.03 6.03 0 0 1 2 8c0-3.314 2.686-6 6-6s6 2.686 6 6Z" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>
            ${fmtNum(p.num_comments)} comments
          </button>
        </div>
      </div>`;

  // Compact layout for link posts (no rich embedded media)
  const isImageDomain = p.domain && (p.domain === 'i.redd.it' || p.domain === 'i.imgur.com' || /^i\.\w/.test(p.domain));
  const isCompact = !p.is_self && !p.is_video && !p.youtube_id && !p.redgifs_id && !p.embed_url && !p.gif_url && !(p.gallery?.length > 1) && !isImageDomain;
  if (isCompact) {
    const imgSrc = p.gallery?.[0]?.url ?? p.preview_img ?? null;
    const thumbContent = imgSrc
      ? `<img src="${escHtml(imgSrc)}" loading="lazy" alt="" onerror="this.style.display='none'">`
      : `<svg width="28" height="28" viewBox="0 0 24 24" fill="none"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    return `
    <div class="post post-compact" style="animation-delay:${delay}ms">
      <div class="post-compact-left">
        <div class="post-header">
          ${metaTop}
          ${titleLink}
        </div>
        ${footer}
      </div>
      <a class="post-compact-thumb" href="${escHtml(p.url)}" target="_blank" rel="noopener">${thumbContent}</a>
    </div>`;
  }

  // Full layout for rich media posts and self/text posts
  const excerptHtml = p.selftext ? `<div class="post-excerpt"><div class="md">${renderMd(p.selftext)}</div></div>` : '';
  const crosspostHtml = p.crosspost_from ? `<div class="crosspost-banner">↪ <a href="javascript:;" data-nav="/r/${escHtml(p.crosspost_from.subreddit)}/comments/${escHtml(p.crosspost_from.id)}">view original</a> · <a href="javascript:;" data-nav="/r/${escHtml(p.crosspost_from.subreddit)}">r/${escHtml(p.crosspost_from.subreddit)}</a></div>` : '';
  return `
    <div class="post" style="animation-delay:${delay}ms">
      <div class="post-header">
        ${metaTop}
        ${titleLink}
        ${crosspostHtml}
      </div>
      ${mediaHtmlCard(p)}
      ${excerptHtml}
      ${footer}
    </div>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// COMMENT TREE
// ═══════════════════════════════════════════════════════════════════════════
const THREAD_MAX_DEPTH = 4;
function renderCommentTree(comments, depth=0, sub='', postId='', postAuthor='') {
  return comments.map(c => {
    const isDeleted = !c.body || c.body==='[deleted]' || c.body==='[removed]';
    const isAutoMod = c.author === 'AutoModerator';
    const startCollapsed = isAutoMod;
    const isOP    = postAuthor && !isDeleted && c.author === postAuthor;
    const isMod   = c.distinguished === 'moderator';
    const isAdmin = c.distinguished === 'admin';

    let repliesHtml = '';
    if (c.replies?.length) {
      if (depth >= THREAD_MAX_DEPTH) {
        const href = `/r/${escHtml(sub)}/comments/${escHtml(postId)}/_/${escHtml(c.id)}`;
        repliesHtml = `<div class="comment-replies"><a class="continue-thread" href="javascript:;" data-nav="${href}">Continue thread →</a></div>`;
      } else {
        repliesHtml = `<div class="comment-replies">${renderCommentTree(c.replies, depth+1, sub, postId, postAuthor)}</div>`;
      }
    }

    return `<div class="comment${isDeleted?' comment-deleted':''}${startCollapsed?' collapsed':''}" data-depth="${depth}">
      <div class="comment-header">
        <button class="comment-collapse">${startCollapsed?'+':'−'}</button>
        <span class="comment-author${isMod?' is-mod':''}" data-user="${escHtml(c.author)}">${escHtml(c.author)}</span>
        ${isMod   ? '<span class="comment-mod">MOD</span>'   : ''}
        ${isAdmin ? '<span class="comment-admin">ADMIN</span>' : ''}
        ${isOP    ? '<span class="comment-op">OP</span>'     : ''}
        ${renderAuthorFlair(c)}
        <span class="comment-score">▲ ${fmtNum(c.score)}</span>
        <span class="comment-time">${timeAgo(c.created_utc)}${c.edited_utc ? ' <span class="edited-mark">*edited</span>' : ''}</span>
      </div>
      <div class="comment-body md">${isDeleted?'<em>[deleted]</em>':renderMd(c.body)}</div>
      ${repliesHtml}
    </div>`;
  }).join('');
}

// Collapse on comment header click
document.getElementById('post-view').addEventListener('click', e => {
  const header = e.target.closest('.comment-header');
  if (!header || e.target.tagName==='A') return;
  // navigate to user profile when clicking author
  const authorEl = e.target.closest('.comment-author[data-user]');
  if (authorEl) { navigateOrOpen(`/user/${authorEl.dataset.user}`, e); return; }
  const comment   = header.closest('.comment');
  const collapsed = comment.classList.toggle('collapsed');
  const btn = comment.querySelector(':scope > .comment-header > .comment-collapse');
  if (btn) btn.textContent = collapsed ? '+' : '−';
});

// ═══════════════════════════════════════════════════════════════════════════
// POST VIEW (full-screen)
// ═══════════════════════════════════════════════════════════════════════════
const postView  = document.getElementById('post-view');
const pvContent = document.getElementById('pv-content');
const pvScroll  = document.getElementById('pv-scroll');
const pvOpen    = document.getElementById('pv-open');
const pvBreadcrumb = document.getElementById('pv-breadcrumb');

function openPostView() { postView.classList.add('open'); document.body.style.overflow='hidden'; }
function closePostView(){ postView.classList.remove('open'); document.body.style.overflow=''; }

document.getElementById('pv-home').addEventListener('click', () => { navigate('/r/popular/hot'); });

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

let currentCommentSort = 'confidence';
let _pvSub = '', _pvPostId = '', _pvCommentId = '';
let _pvData = null;

const COMMENT_SORTS = [
  {value:'confidence',    label:'Best'},
  {value:'top',           label:'Top'},
  {value:'new',           label:'New'},
  {value:'controversial', label:'Controversial'},
  {value:'old',           label:'Old'},
  {value:'qa',            label:'Q&A'},
];

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

async function changeCommentSort(sort) {
  currentCommentSort = sort;
  setActiveButton(pvContent, 'csort', sort);
  const area = pvContent.querySelector('.pv-comments-area');
  if (!area) return;
  area.innerHTML = '<div class="state" style="padding:30px 0"><div class="state-icon">⌗</div><div class="state-title">Loading…</div></div>';
  try {
    const apiUrl = `/api/r/${encodeURIComponent(_pvSub)}/comments/${encodeURIComponent(_pvPostId)}?sort=${sort}${_pvCommentId ? `&comment=${encodeURIComponent(_pvCommentId)}` : ''}`;
    const res  = await fetch(apiUrl);
    if (!res.ok) { area.innerHTML = errState('Failed to load comments', 'comments'); return; }
    const data = await res.json();
    area.innerHTML = buildCommentsHtml(data, _pvCommentId);
  } catch {
    area.innerHTML = errState('Network error', 'comments');
  }
}

async function loadPostView(sub, postId, commentId='') {
  _pvSub = sub; _pvPostId = postId; _pvCommentId = commentId;
  currentCommentSort = 'confidence';
  pvContent.innerHTML = '<div class="state"><div class="state-icon">⌗</div><div class="state-title">Loading…</div></div>';
  pvScroll.scrollTop = 0;
  openPostView();

  pvBreadcrumb.innerHTML = `<a href="javascript:;" data-nav="/r/${escHtml(sub)}">r/${escHtml(sub)}</a>`;
  pvOpen.href = '#';

  try {
    const apiUrl = `/api/r/${encodeURIComponent(sub)}/comments/${encodeURIComponent(postId)}?sort=confidence` + (commentId ? `&comment=${encodeURIComponent(commentId)}` : '');
    const res  = await fetch(apiUrl);
    const data = await res.json();
    if (!res.ok) { pvContent.innerHTML = errState(escHtml(data.error||'Failed to load'), 'post'); return; }

    const p = data.post;
    pvOpen.href = p.permalink;
    pvBreadcrumb.innerHTML = `<a href="javascript:;" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>`;
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
    const bodyHtml = p.selftext?.trim() ? `<div class="pv-body md">${renderMd(p.selftext)}</div>` : '';
    const crosspostHtml = p.crosspost_from ? `<div class="crosspost-banner">↪ cross-posted from <a href="javascript:;" data-nav="/r/${escHtml(p.crosspost_from.subreddit)}">r/${escHtml(p.crosspost_from.subreddit)}</a> · <a href="javascript:;" data-nav="/r/${escHtml(p.crosspost_from.subreddit)}/comments/${escHtml(p.crosspost_from.id)}">view original</a></div>` : '';

    pvContent.innerHTML = `
      <a class="pv-sub-link" href="javascript:;" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>
      ${crosspostHtml}
      ${pvBadges ? `<div class="post-meta-top" style="margin-bottom:10px">${pvBadges}</div>` : ''}
      <h1 class="${titleClass}">${escHtml(p.title)}</h1>
      <div class="pv-meta">
        <span class="up">▲ ${fmtNum(p.score)}</span>
        <span>${p.upvote_ratio}% upvoted</span>
        <button class="meta-item link" data-user="${escHtml(p.author)}">u/${escHtml(p.author)}</button>
        <span>${timeAgo(p.created_utc)}${pvEditedHtml ? ' '+pvEditedHtml : ''}</span>
        <span>${fmtNum(p.num_comments)} comments</span>
        ${!p.is_self && p.domain ? `<a class="meta-item link" href="${escHtml(p.url)}" target="_blank" rel="noopener">${escHtml(p.domain)} ↗</a>` : ''}
        <a class="meta-item link" href="javascript:;" data-nav="/r/${escHtml(p.subreddit)}/duplicates/${escHtml(p.id)}">dupes</a>
      </div>
      ${mediaHtmlFull(p)}
      ${bodyHtml}
      <div class="pv-divider">
        <div class="pv-divider-line"></div>
      </div>
      ${buildCommentSortBar('confidence')}
      <div class="pv-comments-area">
        ${buildCommentsHtml(data, commentId)}
      </div>`;

    initVideos(pvContent);
    initRedgifs(pvContent);
  } catch(err) {
    pvContent.innerHTML = errState('Network error', 'post');
  }
}

// Click author in post meta; comment sort buttons
pvContent.addEventListener('click', e => {
  const retryBtn = e.target.closest('.state-retry-btn[data-retry]');
  if (retryBtn) {
    const t = retryBtn.dataset.retry;
    if (t === 'post') loadPostView(_pvSub, _pvPostId, _pvCommentId);
    else if (t === 'comments') changeCommentSort(currentCommentSort);
    return;
  }
  const csort = e.target.closest('[data-csort]');
  if (csort) { e.preventDefault(); changeCommentSort(csort.dataset.csort); return; }
  const btn = e.target.closest('[data-user]');
  if (btn && !e.target.closest('a')) { e.preventDefault(); navigateOrOpen(`/user/${btn.dataset.user}`, e); }
});

// ═══════════════════════════════════════════════════════════════════════════
// FEED STATE
// ═══════════════════════════════════════════════════════════════════════════
function buildSubSortHtml(sort='top', time='all', sub='') {
  const btns = ['hot','new','top','rising','controversial'].map(s =>
    `<button class="sort-btn${s===sort?' active':''}" data-sort="${s}">${s.charAt(0).toUpperCase()+s.slice(1)}</button>`
  ).join('');
  const sidebarBtn = sub.toLowerCase() === 'popular' ? '' : `<button class="sidebar-toggle" id="sidebar-toggle-btn">sidebar</button>`;
  return btns + (sort==='top'||sort==='controversial' ? buildTimeFilterHtml(time) : '') + sidebarBtn;
}
function buildProfileSortHtml(tab='posts', sort='new', time='all') {
  const tabBtns = `<button class="sort-btn${tab==='posts'?' active':''}" data-ptab="posts">Posts</button><button class="sort-btn${tab==='comments'?' active':''}" data-ptab="comments">Comments</button>`;
  const sorts = tab==='posts' ? ['hot','new','top'] : ['new','top'];
  const sortBtns = sorts.map(s =>
    `<button class="sort-btn${s===sort?' active':''}" data-psort="${s}">${s.charAt(0).toUpperCase()+s.slice(1)}</button>`
  ).join('');
  return tabBtns + `<div style="display:flex;align-items:center;border-left:1px solid var(--b);margin-left:4px;padding-left:8px;gap:2px">` + sortBtns + `</div>` + (sort==='top' ? buildTimeFilterHtml(time) : '');
}
const SEARCH_SORT_BTN_HTML = `
  <button class="sort-btn active" data-ssort="relevance">Relevance</button>
  <button class="sort-btn" data-ssort="hot">Hot</button>
  <button class="sort-btn" data-ssort="top">Top</button>
  <button class="sort-btn" data-ssort="new">New</button>`;
const feed        = document.getElementById('feed');
const sortBar     = document.getElementById('sort-bar');
const ctxInfo     = document.getElementById('ctx-info');
const sentinel    = document.getElementById('scroll-sentinel');
const subInput    = document.getElementById('subreddit-input');
const pvSubInput  = document.getElementById('pv-subreddit-input');

let currentSub    = '';
let currentSort   = 'top';
let currentTime   = 'all';
let afterToken    = null;
let loading       = false;
let feedGen       = 0;
let profileMode   = false;
let profileTab    = 'posts';
let profileSort   = 'new';
let profileTime   = 'all';
let profileUser   = '';
let profileAfter  = null;
let searchMode    = false;
let searchQuery   = '';
let searchSort    = 'relevance';
let searchTime    = 'all';
let searchSub       = '';
let searchSubStored = ''; // remembers last scoped sub for checkbox toggle
let searchNsfw    = false;
let searchAfter   = null;

function showSkeletons() {
  feed.innerHTML = Array.from({length:SKELETON_COUNT}, ()=>`
    <div class="skeleton-post">
      <div class="skel-header"><div class="skel skel-title"></div><div class="skel skel-title2"></div></div>
      <div class="skel skel-banner"></div>
      <div class="skel skel-footer"></div>
    </div>`).join('');
  sentinel.classList.remove('active', 'loading');
}

function errState(msg, retryTarget) {
  return `<div class="state"><div class="state-icon">⚠</div><div class="state-title">${msg}</div><button class="state-retry-btn" data-retry="${retryTarget}">Try again</button></div>`;
}

function retryFeedLoad() {
  if (duplicatesMode) {
    loadDuplicatesPage(duplicatesSub, duplicatesPostId);
  } else if (searchMode) {
    if (searchType === 'communities') loadCommunityResults(searchQuery);
    else if (searchType === 'users')  loadUserResults(searchQuery);
    else loadSearchResults(searchQuery, searchSort, searchTime);
  } else if (profileMode) {
    loadProfileTab(profileUser, profileTab, profileSort, profileTime);
  } else {
    loadSubFeed(currentSub, currentSort, currentTime);
  }
}

// ── Subreddit feed ─────────────────────────────────────────────────────────

async function loadAbout(sub) {
  try {
    const res = await fetch(`/api/r/${encodeURIComponent(sub)}/about`);
    if (!res.ok) return;
    const d = await res.json();
    const letter = escHtml(sub[0].toUpperCase());
    document.getElementById('ctx-icon-wrap').innerHTML = d.icon
      ? `<img class="ctx-icon" src="${escHtml(d.icon)}" alt="" onerror="this.style.display='none'">` : '';
    document.getElementById('ctx-title').textContent = d.title || `r/${sub}`;
    const activePart = d.active ? ` · <span>${fmtNum(d.active)}</span> online` : '';
    document.getElementById('ctx-stats').innerHTML = `<span>${fmtNum(d.subscribers)}</span> members${activePart}`;
    ctxInfo.classList.add('visible');
  } catch {}
}

async function fetchPosts(sub, sort, time, after) {
  let url = `/api/r/${encodeURIComponent(sub)}?sort=${sort}`;
  if (sort === 'top' || sort === 'controversial') url += `&t=${time || 'all'}`;
  if (after) url += `&after=${after}`;
  return fetch(url);
}

async function loadSubFeed(sub, sort, time='all', after=null, append=false) {
  if (append && loading) return;
  if (!append) feedGen++;
  const myGen = feedGen;
  loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    const res  = await fetchPosts(sub, sort, time, after);
    const data = await res.json();
    if (myGen !== feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error||'Error'), 'feed');
      return;
    }
    if (!append) feed.innerHTML = '';
    if (!data.posts.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No posts found</div></div>';
      return;
    }
    const startIdx = append ? feed.querySelectorAll('.post').length : 0;
    const multiSub = currentSub === 'popular' || currentSub === 'all';
    feed.insertAdjacentHTML('beforeend', data.posts.map((p,i)=>renderPost(p,startIdx+i,multiSub)).join(''));
    initVideos(feed);
    initRedgifs(feed);
    afterToken = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === feedGen) loading = false; }
}

async function loadSubreddit(sub, sort='top', time='all') {
  profileMode = false;
  currentSub  = sub.trim();
  currentSort = sort;
  currentTime = time;
  afterToken  = null;
  document.title = `r/${currentSub} — RDVWR`;
  subInput.value = currentSub;
  pvSubInput.value = currentSub;
  sortBar.innerHTML = buildSubSortHtml(sort, time, sub);
  sortBar.style.display = 'flex';
  ctxInfo.classList.remove('visible');
  loadAbout(currentSub);
  await loadSubFeed(currentSub, currentSort, currentTime);
}

// ── Profile feed ───────────────────────────────────────────────────────────

function renderUserCommentCard(c, idx) {
  const delay = Math.min(idx*ANIM_DELAY_STEP, ANIM_DELAY_MAX);
  return `<div class="user-comment-card" style="animation-delay:${delay}ms">
    <div class="ucc-context">
      <span>in <a href="javascript:;" data-nav="/r/${escHtml(c.subreddit)}">r/${escHtml(c.subreddit)}</a></span>
      <span>·</span>
      <a href="${escHtml(c.link_permalink)}" target="_blank" rel="noopener" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(c.link_title)}</a>
    </div>
    <div class="ucc-body md">${renderMd(c.body)}</div>
    <div class="ucc-footer">
      <span class="ucc-score">▲ ${fmtNum(c.score)}</span>
      <span>${timeAgo(c.created_utc)}</span>
    </div>
  </div>`;
}

async function loadProfileTab(username, tab, sort='new', time='all', after=null, append=false) {
  if (append && loading) return;
  if (!append) feedGen++;
  const myGen = feedGen;
  loading = true;
  if (!append) { showSkeletons(); profileAfter = null; }
  else sentinel.classList.add('loading');
  try {
    const endpoint = tab==='posts' ? 'posts' : 'comments';
    let url = `/api/user/${encodeURIComponent(username)}/${endpoint}?sort=${sort}`;
    if (sort === 'top') url += `&t=${time || 'all'}`;
    if (after) url += `&after=${after}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error||'Error'), 'feed');
      return;
    }
    if (!append) feed.innerHTML = '';
    const items = tab==='posts' ? data.posts : data.comments;
    if (!items?.length && !append) {
      feed.innerHTML = `<div class="state"><div class="state-icon">∅</div><div class="state-title">Nothing here</div></div>`;
      return;
    }
    const startIdx = append ? feed.children.length : 0;
    if (tab==='posts') {
      feed.insertAdjacentHTML('beforeend', items.map((p,i)=>renderPost(p,startIdx+i,true)).join(''));
      initVideos(feed);
      initRedgifs(feed);
    } else {
      feed.insertAdjacentHTML('beforeend', items.map((c,i)=>renderUserCommentCard(c,startIdx+i)).join(''));
    }
    profileAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === feedGen) loading = false; }
}

async function loadProfile(username) {
  profileMode = true; profileUser = username; profileTab = 'posts'; profileSort = 'new'; profileTime = 'all'; profileAfter = null;
  sortBar.style.display = 'none';
  ctxInfo.classList.remove('visible');
  subInput.value = '';
  pvSubInput.value = '';
  document.title = `u/${username} — RDVWR`;

  // Load user info
  try {
    const res = await fetch(`/api/user/${encodeURIComponent(username)}/about`);
    if (res.ok) {
      const d = await res.json();
      const letter = escHtml(username[0].toUpperCase());
      document.getElementById('ctx-icon-wrap').innerHTML = d.icon
        ? `<img class="ctx-icon" src="${escHtml(d.icon)}" alt="" onerror="this.style.display='none'">` : '';
      document.getElementById('ctx-title').textContent = `u/${d.name}`;
      document.getElementById('ctx-stats').innerHTML =
        `<span>${fmtNum(d.karma_post)}</span> post karma · <span>${fmtNum(d.karma_comment)}</span> comment karma · joined ${fmtDate(d.created_utc)}`;
      ctxInfo.classList.add('visible');
    }
  } catch {}

  sortBar.innerHTML = buildProfileSortHtml(profileTab, profileSort, profileTime);
  sortBar.style.display = 'flex';

  await loadProfileTab(username, 'posts', profileSort, profileTime);
}

// ── Search feed ────────────────────────────────────────────────────────────

async function loadSearchResults(query, sort, time, after=null, append=false) {
  if (append && loading) return;
  if (!append) feedGen++;
  const myGen = feedGen;
  loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    let url = `/api/search?q=${encodeURIComponent(query)}&sort=${sort}&t=${time}`;
    if (searchSub)  url += `&sub=${encodeURIComponent(searchSub)}`;
    if (searchNsfw) url += `&nsfw=1`;
    if (after)      url += `&after=${encodeURIComponent(after)}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error||'Search failed'), 'feed');
      return;
    }
    if (!append) feed.innerHTML = '';
    if (!data.posts.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No results found</div></div>';
      return;
    }
    const startIdx = append ? feed.querySelectorAll('.post').length : 0;
    feed.insertAdjacentHTML('beforeend', data.posts.map((p,i)=>renderPost(p,startIdx+i,true)).join(''));
    initVideos(feed);
    initRedgifs(feed);
    searchAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen === feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen === feedGen) loading = false; }
}

function buildTimeFilterHtml(selected) {
  return `<div class="time-filter-wrap"><select class="time-filter" id="time-filter">
    <option value="all"${selected==='all'?' selected':''}>All time</option>
    <option value="year"${selected==='year'?' selected':''}>Past year</option>
    <option value="month"${selected==='month'?' selected':''}>Past month</option>
    <option value="week"${selected==='week'?' selected':''}>Past week</option>
    <option value="day"${selected==='day'?' selected':''}>Today</option>
  </select></div>`;
}

async function loadSearch(query, sort='relevance', time='all', sub='', nsfw=true, type='posts') {
  // Track the scoped sub for checkbox toggle: update when sub changes, reset on new query
  if (sub) searchSubStored = sub;
  else if (query !== searchQuery) searchSubStored = '';

  searchMode  = true;
  profileMode = false;
  searchQuery = query;
  searchSort  = sort;
  searchTime  = time;
  searchSub   = sub;
  searchNsfw  = nsfw;
  searchAfter = null;
  afterToken  = null;
  communityAfter = null;
  userAfter   = null;
  searchType  = type;
  subInput.value = query;
  pvSubInput.value = query;
  document.title = `Search: ${query}${sub ? ` in r/${sub}` : ''} — RDVWR`;

  document.getElementById('ctx-icon-wrap').innerHTML = '';
  document.getElementById('ctx-title').textContent = sub ? `r/${sub}: "${query}"` : `Search: "${query}"`;
  document.getElementById('ctx-stats').innerHTML = `<span>${sort}</span>${time !== 'all' ? ` · <span>${time}</span>` : ''}`;
  ctxInfo.classList.add('visible');

  const nsfwToggleHtml  = `<button class="nsfw-toggle${nsfw?' active':''}" id="nsfw-toggle">18+</button>`;
  const scopeCheckHtml  = searchSubStored
    ? `<label class="scope-check-label"><input type="checkbox" class="scope-check-input" id="scope-check"${sub ? ' checked' : ''}><span>r/${escHtml(searchSubStored)}</span></label>`
    : '';
  sortBar.innerHTML = SEARCH_SORT_BTN_HTML + (sort === 'top' ? buildTimeFilterHtml(time) : '') + nsfwToggleHtml + scopeCheckHtml;
  sortBar.style.display = 'flex';
  setActiveButton(sortBar, 'ssort', sort);

  // Flair searches and community-scoped searches are posts-only — hide type tabs
  const isScopedSearch = !!sub || query.includes('flair:');
  if (isScopedSearch) {
    searchTypeBar.style.display = 'none';
    searchType = 'posts';
    type = 'posts';
  } else {
    searchTypeBar.style.display = 'flex';
    setActiveButton(searchTypeBar, 'stype', type);
  }
  sortBar.style.display = type === 'posts' ? 'flex' : 'none';

  if (type === 'communities') { await loadCommunityResults(query); }
  else if (type === 'users')  { await loadUserResults(query); }
  else                        { await loadSearchResults(query, sort, time); }
}

// ── Duplicates page ────────────────────────────────────────────────────────

let duplicatesMode = false;
let duplicatesSub  = '';
let duplicatesPostId = '';
let duplicatesAfter  = null;

async function loadDuplicatesPage(sub, postId, after=null, append=false) {
  if (append && loading) return;
  if (!append) feedGen++;
  const myGen = feedGen;
  loading = true;
  duplicatesMode  = true;
  duplicatesSub   = sub;
  duplicatesPostId = postId;
  if (!append) {
    duplicatesAfter = null;
    showSkeletons();
    sortBar.style.display = 'none';
    ctxInfo.classList.remove('visible');
    subInput.value = '';
    pvSubInput.value = '';
  } else {
    sentinel.classList.add('loading');
  }
  try {
    let url = `/api/r/${encodeURIComponent(sub)}/duplicates/${encodeURIComponent(postId)}`;
    if (after) url += `?after=${encodeURIComponent(after)}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== feedGen) return;
    if (!res.ok) {
      if (!append) feed.innerHTML = errState(escHtml(data.error || 'Failed to load'), 'feed');
      return;
    }
    if (!append) {
      const p = data.post;
      if (p) {
        document.title = `Duplicates: ${p.title} — RDVWR`;
        document.getElementById('ctx-icon-wrap').innerHTML = '';
        document.getElementById('ctx-title').textContent = p.title;
        document.getElementById('ctx-stats').innerHTML =
          `<a class="ctx-sub-link" href="javascript:;" data-nav="/r/${escHtml(p.subreddit)}">r/${escHtml(p.subreddit)}</a>`;
        ctxInfo.classList.add('visible');
      } else {
        document.title = `Duplicates — RDVWR`;
      }
      const backSub  = escHtml(sub);
      const backId   = escHtml(postId);
      const titleTxt = data.post ? escHtml(data.post.title) : 'post';
      feed.innerHTML = `<div class="dupes-header">
        <a class="dupes-back" href="javascript:;" data-nav="/r/${backSub}/comments/${backId}">← back to post</a>
        <span class="dupes-count">${data.posts.length} other post${data.posts.length !== 1 ? 's' : ''} linking to this URL</span>
      </div>`;
      if (!data.posts.length) {
        feed.insertAdjacentHTML('beforeend', '<div class="state"><div class="state-icon">∅</div><div class="state-title">No duplicates found</div></div>');
        return;
      }
    }
    const startIdx = append ? feed.querySelectorAll('.post').length : 0;
    feed.insertAdjacentHTML('beforeend', data.posts.map((p, i) => renderPost(p, startIdx + i, true)).join(''));
    initVideos(feed);
    initRedgifs(feed);
    duplicatesAfter = data.after;
    sentinel.classList.remove('loading');
  } catch {
    if (!append && myGen === feedGen) feed.innerHTML = errState('Network error', 'feed');
  } finally {
    if (myGen === feedGen) loading = false;
  }
}

// ── Sidebar ────────────────────────────────────────────────────────────────

const sidebarPanel = document.getElementById('sidebar-panel');
const sidebarInner = document.getElementById('sidebar-inner');
let sidebarOpen    = false;
let sidebarSub     = '';
let sidebarLoaded  = false;
const _sidebarCache = new Map();
const SIDEBAR_CACHE_TTL = 5 * 60 * 1000;

function closeSidebar() {
  sidebarOpen = false;
  sidebarPanel.classList.remove('open');
  const btn = document.getElementById('sidebar-toggle-btn');
  if (btn) btn.classList.remove('active');
}

async function toggleSidebar(sub) {
  if (sidebarOpen && sidebarSub === sub) { closeSidebar(); return; }
  sidebarSub = sub;
  sidebarOpen = true;
  sidebarPanel.classList.add('open');
  const btn = document.getElementById('sidebar-toggle-btn');
  if (btn) btn.classList.add('active');

  const cached = _sidebarCache.get(sub);
  if (cached && Date.now() - cached.ts < SIDEBAR_CACHE_TTL) {
    sidebarInner.innerHTML = cached.html;
    return;
  }

  sidebarInner.innerHTML = '<div style="padding:10px 0;font-family:var(--mono);font-size:11px;color:var(--tx3)">Loading…</div>';

  try {
    const [aboutRes, rulesRes] = await Promise.all([
      fetch(`/api/r/${encodeURIComponent(sub)}/about`),
      fetch(`/api/r/${encodeURIComponent(sub)}/rules`),
    ]);
    const about = aboutRes.ok ? await aboutRes.json() : {};
    const rulesData = rulesRes.ok ? await rulesRes.json() : {rules:[]};

    let html = '';
    if (about.description) {
      html += `<div class="sidebar-section">
        <div class="sidebar-section-title">About</div>
        <div class="sidebar-desc md">${renderMd(about.sidebar || about.description)}</div>
      </div>`;
    }
    if (rulesData.rules?.length) {
      const rulesHtml = rulesData.rules.map((r, i) =>
        `<li class="sidebar-rule"><span class="sidebar-rule-num">${i+1}.</span>${escHtml(r.short_name)}</li>`
      ).join('');
      html += `<div class="sidebar-section">
        <div class="sidebar-section-title">Rules</div>
        <ul class="sidebar-rules">${rulesHtml}</ul>
      </div>`;
    }
    if (!html) html = '<div style="font-family:var(--mono);font-size:11px;color:var(--tx3)">No sidebar content.</div>';
    _sidebarCache.set(sub, { html, ts: Date.now() });
    sidebarInner.innerHTML = html;
  } catch {
    sidebarInner.innerHTML = '<div style="font-family:var(--mono);font-size:11px;color:var(--tx3)">Failed to load sidebar.</div>';
  }
}

// ── Search: communities & users ────────────────────────────────────────────

const searchTypeBar = document.getElementById('search-type-bar');
let searchType      = 'posts';  // 'posts' | 'communities' | 'users'
let communityAfter  = null;
let userAfter       = null;

function renderCommunityCard(c, idx) {
  const delay = Math.min(idx*ANIM_DELAY_STEP, ANIM_DELAY_MAX);
  const letter = escHtml((c.name||'?')[0].toUpperCase());
  const iconHtml = c.icon
    ? `<img src="${escHtml(c.icon)}" alt="" onerror="this.outerHTML='<span>${letter}</span>'">`
    : `<span>${letter}</span>`;
  return `<div class="community-card" style="animation-delay:${delay}ms" data-nav="/r/${escHtml(c.name)}">
    <div class="community-card-icon">${iconHtml}</div>
    <div class="community-card-body">
      <div class="community-card-name">r/${escHtml(c.name)}</div>
      ${c.title ? `<div class="community-card-title">${escHtml(c.title)}</div>` : ''}
      ${c.description ? `<div class="community-card-desc">${escHtml(c.description)}</div>` : ''}
      <div class="community-card-stats"><span>${fmtNum(c.subscribers||0)}</span> members${c.over_18 ? ' · <span style="color:#ff5050">nsfw</span>' : ''}</div>
    </div>
  </div>`;
}

function renderUserCard(u, idx) {
  const delay = Math.min(idx*ANIM_DELAY_STEP, ANIM_DELAY_MAX);
  const letter = escHtml((u.name||'?')[0].toUpperCase());
  const iconHtml = u.icon
    ? `<img src="${escHtml(u.icon)}" alt="" onerror="this.outerHTML='<span>${letter}</span>'">`
    : `<span>${letter}</span>`;
  return `<div class="user-card" style="animation-delay:${delay}ms" data-nav="/user/${escHtml(u.name)}">
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

async function loadCommunityResults(query, after=null, append=false) {
  if (append && loading) return;
  if (!append) feedGen++;
  const myGen = feedGen;
  loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    let url = `/api/search/communities?q=${encodeURIComponent(query)}`;
    if (after) url += `&after=${encodeURIComponent(after)}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== feedGen) return;
    if (!append) feed.innerHTML = '';
    if (!data.communities?.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No communities found</div></div>';
      return;
    }
    const startIdx = append ? feed.children.length : 0;
    feed.insertAdjacentHTML('beforeend', data.communities.map((c,i)=>renderCommunityCard(c,startIdx+i)).join(''));
    communityAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen===feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen===feedGen) loading = false; }
}

async function loadUserResults(query, after=null, append=false) {
  if (append && loading) return;
  if (!append) feedGen++;
  const myGen = feedGen;
  loading = true;
  if (!append) showSkeletons();
  else sentinel.classList.add('loading');
  try {
    let url = `/api/search/users?q=${encodeURIComponent(query)}`;
    if (after) url += `&after=${encodeURIComponent(after)}`;
    const res  = await fetch(url);
    const data = await res.json();
    if (myGen !== feedGen) return;
    if (!append) feed.innerHTML = '';
    if (!data.users?.length && !append) {
      feed.innerHTML = '<div class="state"><div class="state-icon">∅</div><div class="state-title">No users found</div></div>';
      return;
    }
    const startIdx = append ? feed.children.length : 0;
    feed.insertAdjacentHTML('beforeend', data.users.map((u,i)=>renderUserCard(u,startIdx+i)).join(''));
    userAfter = data.after;
    sentinel.classList.remove('loading');
  } catch { if (!append && myGen===feedGen) feed.innerHTML = errState('Network error', 'feed'); }
  finally  { if (myGen===feedGen) loading = false; }
}

// ═══════════════════════════════════════════════════════════════════════════
// ROUTER
// ═══════════════════════════════════════════════════════════════════════════
const SORTS = new Set(['hot','new','top','rising','controversial','best']);

function parseRoute(path=location.pathname) {
  const pathname = path.split('?')[0];
  const mDupes = pathname.match(/^\/r\/([^\/]+)\/duplicates\/([^\/]+)/i);
  if (mDupes) return { type: 'duplicates', sub: mDupes[1], postId: mDupes[2] };
  const mPost = pathname.match(/^\/r\/([^\/]+)\/comments\/([^\/]+)(?:\/[^\/]*(?:\/([a-z0-9]+))?)?/i);
  if (mPost) return { type:'post', sub:mPost[1], postId:mPost[2], commentId:mPost[3]||'' };
  const mSub  = pathname.match(/^\/r\/([^\/]+)(?:\/([^\/]+))?/);
  if (mSub) {
    const sort = SORTS.has(mSub[2]) ? mSub[2] : (mSub[1].toLowerCase() === 'popular' ? 'hot' : 'top');
    const qs = path.includes('?') ? path.split('?')[1] : location.search.slice(1);
    const time = new URLSearchParams(qs).get('t') || (sort === 'top' ? 'day' : 'all');
    return { type:'sub', sub:mSub[1], sort, time };
  }
  const mUser = pathname.match(/^\/u(?:ser)?\/([^\/]+)/);
  if (mUser) return { type:'user', username:mUser[1] };
  if (pathname === '/search') {
    const qs = path.includes('?') ? path.split('?')[1] : location.search.slice(1);
    const params = new URLSearchParams(qs);
    const q = params.get('q') || '';
    if (q) return { type:'search', query:q, sort:params.get('sort')||'relevance', time:params.get('t')||'all', sub:params.get('sub')||'', nsfw:params.get('nsfw')!=='0', stype:params.get('stype')||'posts' };
  }
  return { type:'home' };
}

function navigateOrOpen(path, e) {
  if (e && (e.ctrlKey || e.metaKey || e.button === 1)) { window.open(path, '_blank'); return; }
  navigate(path);
}

function navigate(path, { replace=false }={}) {
  history.replaceState({ ...(history.state||{}), scrollY: window.scrollY }, '', location.href);
  if (replace) history.replaceState(null,'',path);
  else         history.pushState(null,'',path);
  renderRoute(parseRoute(path));
}

async function renderRoute(route, { restoreScroll=0 }={}) {
  if (route.type !== 'search') {
    searchTypeBar.style.display = 'none';
    searchType = 'posts';
  }
  if (route.type !== 'duplicates') duplicatesMode = false;
  switch (route.type) {
    case 'home':
      navigate('/r/popular/hot', { replace: true });
      return;
    case 'sub':
      closePostView();
      closeSidebar();
      searchMode = false;
      await loadSubreddit(route.sub, route.sort, route.time || 'all');
      break;
    case 'post':
      if (!feed.querySelector('.post')) await loadSubreddit(route.sub, currentSort);
      await loadPostView(route.sub, route.postId, route.commentId||'');
      break;
    case 'user':
      closePostView();
      closeSidebar();
      searchMode = false;
      await loadProfile(route.username);
      break;
    case 'search':
      closePostView();
      closeSidebar();
      await loadSearch(route.query, route.sort, route.time, route.sub, route.nsfw, route.stype || 'posts');
      break;
    case 'duplicates':
      closePostView();
      closeSidebar();
      searchMode = false;
      profileMode = false;
      await loadDuplicatesPage(route.sub, route.postId);
      break;
  }
  if (route.type !== 'post') window.scrollTo({top: restoreScroll, behavior: 'instant'});
}

window.addEventListener('popstate', (e) => {
  const savedScroll = e.state?.scrollY || 0;
  const route = parseRoute();
  if (route.type !== 'post') closePostView();
  // Going back to a sub — if the feed already has posts for this sub, just restore scroll
  const hasFeedPosts = !!feed.querySelector('.post');
  if (route.type === 'sub' && hasFeedPosts && route.sub === currentSub && !searchMode && !profileMode && !duplicatesMode) {
    currentSort = route.sort;
    currentTime = route.time || 'all';
    window.scrollTo({top: savedScroll, behavior: 'instant'});
    return;
  }
  renderRoute(route, { restoreScroll: savedScroll });
});

// ═══════════════════════════════════════════════════════════════════════════
// EVENT WIRING
// ═══════════════════════════════════════════════════════════════════════════

// Feed clicks: comments button, author link, retry button
feed.addEventListener('click', e => {
  if (e.defaultPrevented) return;
  const retryBtn = e.target.closest('.state-retry-btn[data-retry]');
  if (retryBtn) { retryFeedLoad(); return; }
  const commBtn = e.target.closest('.comments-link[data-id]');
  const userBtn = e.target.closest('.post-author[data-user]');
  if (commBtn) {
    navigateOrOpen(`/r/${commBtn.dataset.sub}/comments/${commBtn.dataset.id}`, e);
  } else if (userBtn) {
    navigateOrOpen(`/user/${userBtn.dataset.user}`, e);
  }
});

// Post-view content: author clicks (links handled by capture-phase global handler)

function buildSearchUrl(q=searchQuery, sort=searchSort, time=searchTime, sub=searchSub, nsfw=searchNsfw) {
  let url = `/search?q=${encodeURIComponent(q)}&sort=${sort}`;
  if (time !== 'all') url += `&t=${time}`;
  if (sub)  url += `&sub=${encodeURIComponent(sub)}`;
  if (!nsfw) url += `&nsfw=0`;
  return url;
}

// Search type tab bar
searchTypeBar.addEventListener('click', e => {
  const btn = e.target.closest('[data-stype]');
  if (!btn || !searchMode) return;
  const t = btn.dataset.stype;
  if (t === searchType) return;
  searchType = t;
  setActiveButton(searchTypeBar, 'stype', t);
  sortBar.style.display = t === 'posts' ? 'flex' : 'none';
  if (t === 'communities') loadCommunityResults(searchQuery);
  else if (t === 'users')  loadUserResults(searchQuery);
  else                     loadSearchResults(searchQuery, searchSort, searchTime);
});

// Sort buttons (subreddit mode + search mode + profile mode)
sortBar.addEventListener('click', e => {
  // Sidebar toggle
  if (e.target.closest('#sidebar-toggle-btn')) {
    toggleSidebar(currentSub);
    return;
  }
  if (e.target.closest('#nsfw-toggle') && searchMode) {
    searchNsfw = !searchNsfw;
    navigate(buildSearchUrl(), { replace:true });
    return;
  }
  const ssortBtn = e.target.closest('.sort-btn[data-ssort]');
  if (ssortBtn && searchMode) {
    const newSort = ssortBtn.dataset.ssort;
    if (newSort === searchSort) return;
    searchSort = newSort; searchTime = 'all';
    navigate(buildSearchUrl(), { replace:true });
    return;
  }
  // Profile tab switch
  const ptabBtn = e.target.closest('.sort-btn[data-ptab]');
  if (ptabBtn && profileMode) {
    if (ptabBtn.dataset.ptab === profileTab) return;
    profileTab = ptabBtn.dataset.ptab;
    profileSort = 'new'; profileTime = 'all';
    sortBar.innerHTML = buildProfileSortHtml(profileTab, profileSort, profileTime);
    loadProfileTab(profileUser, profileTab, profileSort, profileTime);
    return;
  }
  // Profile sort
  const psortBtn = e.target.closest('.sort-btn[data-psort]');
  if (psortBtn && profileMode) {
    const newSort = psortBtn.dataset.psort;
    if (newSort === profileSort) return;
    profileSort = newSort; profileTime = 'all';
    sortBar.innerHTML = buildProfileSortHtml(profileTab, profileSort, profileTime);
    loadProfileTab(profileUser, profileTab, profileSort, profileTime);
    return;
  }
  // Subreddit sort
  const sortBtn = e.target.closest('.sort-btn[data-sort]');
  if (!sortBtn || profileMode || searchMode) return;
  const newSort = sortBtn.dataset.sort;
  if (newSort === currentSort) return;
  currentSort = newSort; currentTime = newSort === 'controversial' ? 'day' : 'all';
  afterToken = null;
  window.scrollTo({top:0, behavior:'instant'});
  navigate(`/r/${currentSub}/${currentSort}`, { replace:true });
});

sortBar.addEventListener('change', e => {
  const scopeCheck = e.target.closest('#scope-check');
  if (scopeCheck && searchMode) {
    searchSub = scopeCheck.checked ? searchSubStored : '';
    navigate(buildSearchUrl(), { replace:true });
    return;
  }
  const sel = e.target.closest('#time-filter');
  if (!sel) return;
  if (searchMode) {
    searchTime = sel.value;
    navigate(buildSearchUrl(), { replace:true });
  } else if (profileMode) {
    profileTime = sel.value;
    sortBar.innerHTML = buildProfileSortHtml(profileTab, profileSort, profileTime);
    loadProfileTab(profileUser, profileTab, profileSort, profileTime);
  } else {
    currentTime = sel.value;
    afterToken = null;
    window.scrollTo({top:0, behavior:'instant'});
    navigate(`/r/${currentSub}/${currentSort}?t=${currentTime}`, { replace:true });
  }
});

// Search
function handleSearchInput(e) {
  const pvInput = pvSubInput;
  const mainInput = subInput;
  const activeInput = (e?.currentTarget?.id === 'pv-search-btn' || e?.target === pvInput || document.activeElement === pvInput) ? pvInput : mainInput;
  const val = activeInput.value.trim();
  if (!val) return;
  if (val.startsWith('r/')) {
    const sub = val.slice(2).replace(/^\//, '');
    if (sub) navigate(`/r/${sub}/top`);
  } else {
    const sub = searchMode ? searchSub : (currentSub || '');
    let url = `/search?q=${encodeURIComponent(val)}`;
    if (sub) url += `&sub=${encodeURIComponent(sub)}`;
    navigate(url);
  }
}
function hideAllAutocomplete() {
  document.querySelectorAll('.autocomplete-dropdown').forEach(el => {
    el.classList.remove('open');
    el.innerHTML = '';
  });
}
document.getElementById('search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
document.getElementById('subreddit-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') handleSearchInput();
});
document.getElementById('pv-search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
document.getElementById('pv-subreddit-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') handleSearchInput(e);
});

// Load more
function loadMore() {
  if (loading) return;
  if (duplicatesMode) {
    if (duplicatesAfter) loadDuplicatesPage(duplicatesSub, duplicatesPostId, duplicatesAfter, true);
  } else if (searchMode) {
    if (searchType === 'communities' && communityAfter) loadCommunityResults(searchQuery, communityAfter, true);
    else if (searchType === 'users' && userAfter)       loadUserResults(searchQuery, userAfter, true);
    else if (searchAfter)                               loadSearchResults(searchQuery, searchSort, searchTime, searchAfter, true);
  } else if (profileMode) {
    if (profileAfter) loadProfileTab(profileUser, profileTab, profileSort, profileTime, profileAfter, true);
  } else {
    if (afterToken) loadSubFeed(currentSub, currentSort, currentTime, afterToken, true);
  }
}

new IntersectionObserver(entries => {
  if (entries[0].isIntersecting) loadMore();
}, { rootMargin: '300px' }).observe(sentinel);

// Flair click → filter by flair in subreddit
// Community/User card clicks
feed.addEventListener('click', e => {
  const flairEl = e.target.closest('.flair.flair-clickable[data-flair]');
  if (flairEl) {
    e.stopPropagation();
    const sub   = flairEl.dataset.sub;
    const flair = flairEl.dataset.flair;
    if (sub && flair) navigateOrOpen(`/search?q=${encodeURIComponent('flair:"'+flair+'"')}&sub=${encodeURIComponent(sub)}&sort=new`, e);
    return;
  }
  const card = e.target.closest('.community-card[data-nav], .user-card[data-nav]');
  if (card) { navigateOrOpen(card.dataset.nav, e); return; }
});

// Logo → home
document.getElementById('logo-btn').addEventListener('click', () => navigate('/'));

// Shared link-intercept logic — returns true if it handled the navigation.
function interceptNavLink(a, e) {
  if (a.getAttribute('data-back')) { e.preventDefault(); history.back(); return true; }
  // data-nav="/path" means it's a guaranteed in-app link; href is "javascript:;"
  const datanav = a.getAttribute('data-nav');
  if (datanav) { e.preventDefault(); navigateOrOpen(datanav, e); return true; }

  const href = a.getAttribute('href') || '';
  if (!href || href.startsWith('#') || href.startsWith('javascript:') ||
      href.startsWith('mailto:') || href.startsWith('tel:')) return false;

  const redditPost = href.match(/(?:https?:\/\/(?:www\.)?reddit\.com)\/r\/([^\/]+)\/comments\/([^\/?\s#]+)/);
  if (redditPost) { e.preventDefault(); navigateOrOpen(`/r/${redditPost[1]}/comments/${redditPost[2]}`, e); return true; }
  const redditSub  = href.match(/(?:https?:\/\/(?:www\.)?reddit\.com)\/r\/([^\/?\s#]+)/);
  if (redditSub)  { e.preventDefault(); navigateOrOpen(`/r/${redditSub[1]}`, e); return true; }
  const redditUser = href.match(/(?:https?:\/\/(?:www\.)?reddit\.com)\/u(?:ser)?\/([^\/?\s#]+)/);
  if (redditUser) { e.preventDefault(); navigateOrOpen(`/user/${redditUser[1]}`, e); return true; }
  try {
    const url = new URL(href, location.origin);
    if (url.origin !== location.origin) return false;
    e.preventDefault();
    navigateOrOpen(url.pathname + url.search, e);
    return true;
  } catch { return false; }
}

// Long-press on a post card → open in new tab (mobile)
let _longPressTimer = null;
let _longPressTriggered = false;
document.addEventListener('touchstart', e => {
  _longPressTriggered = false;
  const post = e.target.closest('#feed .post, #feed .post-compact');
  if (!post || e.target.closest('a, button, video, iframe, input')) return;
  const titleLink = post.querySelector('a[data-nav]');
  if (!titleLink) return;
  _longPressTimer = setTimeout(() => {
    _longPressTimer = null;
    _longPressTriggered = true;
    if (navigator.vibrate) navigator.vibrate(40);
    window.open(titleLink.dataset.nav, '_blank');
  }, 550);
}, { passive: true });
document.addEventListener('touchmove', e => {
  if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
}, { passive: true });
document.addEventListener('touchend', e => {
  if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
}, { passive: true });

// iOS PWA fix: intercept in-app links on touchend, BEFORE the browser can open a
// new standalone window. touchend fires before click, and preventDefault here
// suppresses both the click event and native link navigation.
let _touchStartX = 0, _touchStartY = 0, _navFromTouch = false;
document.addEventListener('touchstart', e => {
  _touchStartX = e.touches[0].clientX;
  _touchStartY = e.touches[0].clientY;
  _navFromTouch = false;
}, { passive: true });
document.addEventListener('touchend', e => {
  const dx = Math.abs(e.changedTouches[0].clientX - _touchStartX);
  const dy = Math.abs(e.changedTouches[0].clientY - _touchStartY);
  if (dx > TOUCH_MOVE_THRESHOLD || dy > TOUCH_MOVE_THRESHOLD) return; // scroll/swipe — not a tap
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank') return;
  if (interceptNavLink(a, e)) _navFromTouch = true;
}, { passive: false });

// Capture-phase click handler — handles non-touch (desktop) and reddit.com link rewrites.
document.addEventListener('click', e => {
  if (_navFromTouch) { _navFromTouch = false; return; } // already handled by touchend
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank') return;
  interceptNavLink(a, e);
}, true);

// Middle-click on in-app links → open in new tab.
document.addEventListener('auxclick', e => {
  if (e.button !== 1) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank') return;
  interceptNavLink(a, e);
}, true);

// Unmute all videos when user unmutes or raises volume on any one video.
// Future videos loaded while userPrefersMuted=false also start unmuted.
let _propagatingUnmute = false;
document.addEventListener('volumechange', e => {
  const v = e.target;
  if (v.tagName !== 'VIDEO' || _propagatingUnmute) return;
  if (v.muted && v.volume > 0) {
    if (userPrefersMuted) {
      v.muted = false; // iOS: raise volume while muted → unmute (fires volumechange again)
    } else {
      // user clicked mute on an unmuted video → save muted preference
      userPrefersMuted = true;
      localStorage.setItem('mutePreference', 'muted');
    }
  } else if (!v.muted) {
    userPrefersMuted = false;
    localStorage.setItem('mutePreference', 'unmuted');
    _propagatingUnmute = true;
    document.querySelectorAll('video').forEach(other => { other.muted = false; });
    _propagatingUnmute = false;
  }
}, true);

// ── Lightbox ──────────────────────────────────────────────────────────────
const lightbox    = document.getElementById('lightbox');
const lightboxImg = document.getElementById('lightbox-img');

function openLightbox(src) {
  lightboxImg.src = src;
  lightbox.classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeLightbox() {
  lightbox.classList.remove('open');
  document.body.style.overflow = '';
  lightboxImg.src = '';
}
lightbox.addEventListener('click', closeLightbox);
lightboxImg.addEventListener('click', e => e.stopPropagation());
document.addEventListener('keydown', e => { if (e.key === 'Escape' && lightbox.classList.contains('open')) closeLightbox(); });

document.addEventListener('click', e => {
  const img = e.target.closest('.post-media img, .pv-media img, .md img, .gallery-main-img');
  if (!img) return;
  e.stopPropagation();
  openLightbox(img.src);
});

// ── Subreddit autocomplete ────────────────────────────────────────────────
function setupAutocomplete(inputEl, dropdownEl) {
  let acTimer = null;
  let acIdx = -1;
  let preAcVal = '';

  function hide() { dropdownEl.classList.remove('open'); dropdownEl.innerHTML = ''; acIdx = -1; }
  function show(names) {
    if (!names.length) { hide(); return; }
    acIdx = -1;
    dropdownEl.innerHTML = names.map(n =>
      `<div class="autocomplete-item" data-sub="${escHtml(n)}">${escHtml(n)}</div>`
    ).join('');
    dropdownEl.classList.add('open');
  }

  inputEl.addEventListener('input', () => {
    clearTimeout(acTimer);
    const val = inputEl.value.trim();
    const query = val.startsWith('r/') ? val.slice(2) : val;
    if (query.length < 2) { hide(); return; }
    acTimer = setTimeout(async () => {
      try {
        const res  = await fetch(`/api/subreddit-search?q=${encodeURIComponent(query)}`);
        const data = await res.json();
        show(data.names || []);
      } catch {}
    }, AUTOCOMPLETE_DEBOUNCE);
  });

  inputEl.addEventListener('keydown', e => {
    if (!dropdownEl.classList.contains('open')) return;
    const items = [...dropdownEl.querySelectorAll('.autocomplete-item')];
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (acIdx === -1) preAcVal = inputEl.value;
      acIdx = Math.min(acIdx + 1, items.length - 1);
      items.forEach((item, i) => item.classList.toggle('focused', i === acIdx));
      if (acIdx >= 0) inputEl.value = items[acIdx].dataset.sub;
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      acIdx = Math.max(acIdx - 1, -1);
      items.forEach((item, i) => item.classList.toggle('focused', i === acIdx));
      inputEl.value = acIdx >= 0 ? items[acIdx].dataset.sub : preAcVal;
    } else if (e.key === 'Enter') {
      hide();
      if (acIdx >= 0) {
        e.preventDefault();
        e.stopImmediatePropagation();
        const sub = items[acIdx]?.dataset.sub;
        if (sub) navigate(`/r/${sub}`);
      }
    } else if (e.key === 'Escape') {
      e.stopImmediatePropagation();
      hide();
    }
  }, true);

  inputEl.addEventListener('blur', () => { setTimeout(hide, 150); });
  dropdownEl.addEventListener('mousedown', e => e.preventDefault());
  dropdownEl.addEventListener('click', e => {
    const item = e.target.closest('.autocomplete-item');
    if (!item) return;
    hide();
    navigate(`/r/${item.dataset.sub}`);
  });
}

setupAutocomplete(subInput,   document.getElementById('autocomplete-dropdown'));
setupAutocomplete(pvSubInput, document.getElementById('pv-autocomplete-dropdown'));

// ── Boot ──────────────────────────────────────────────────────────────────
renderRoute(parseRoute());
