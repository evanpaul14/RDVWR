import { escHtml, fmtNum, fmtDate, fmtDateTime, timeAgo, setActiveButton, renderFlair, renderAwards, renderAuthorFlair, ANIM_DELAY_STEP, ANIM_DELAY_MAX } from './utils.js';
import { mediaHtmlCard, mediaHtmlFull, nsfwWrap } from './media.js';

const THREAD_MAX_DEPTH = 4;

// ── Markdown ─────────────────────────────────────────────────────────────────
const mdRenderer = new marked.Renderer();
const _img  = mdRenderer.image.bind(mdRenderer);
const _link = mdRenderer.link.bind(mdRenderer);
mdRenderer.image = (href, title, text) => {
  if (href?.startsWith('giphy|'))   return `<img src="https://media.giphy.com/media/${href.slice(6)}/giphy.gif" alt="${text||'gif'}" loading="lazy">`;
  if (href?.startsWith('redgifs|')) return `<div class="md-gif-embed redgifs-wrap" data-rgid="${href.slice(8)}"><div class="rg-loading"></div></div>`;
  try {
    const h = new URL(href).hostname;
    if (h === 'preview.redd.it' || h === 'external-preview.redd.it')
      href = `/api/img?url=${encodeURIComponent(href)}`;
  } catch (_) {}
  return _img(href, title, text);
};
mdRenderer.link = (href, title, text) => {
  if (href && /\.(jpe?g|gif|png|webp|avif)(\?|$)/i.test(href) && (!text || text === href))
    return `<a href="${href}" target="_blank" rel="noopener"><img src="${href}" alt="" loading="lazy"></a>`;
  const base = _link(href, title, text) || '';
  // Relative links and reddit.com links are intercepted by the SPA router — no _blank
  if (!href || !/^https?:\/\//i.test(href) || /reddit\.com\//i.test(href))
    return base;
  return base.replace('<a ', '<a target="_blank" rel="noopener" ');
};
marked.use({ renderer: mdRenderer, breaks: true, gfm: true });

export function linkifyReddit(text) {
  return text
    .replace(/(`[^`]*`|\[[^\]]*\]\([^\)]*\))|(?<![\w/])(\/?)r\/([A-Za-z0-9_]+(?:\/comments\/[A-Za-z0-9_]+)?)/g,
      (m, skip, slash, sub) => skip ? skip : `[r/${sub}](/r/${sub})`)
    .replace(/(`[^`]*`|\[[^\]]*\]\([^\)]*\))|(?<![\w/])(\/?)u\/([A-Za-z0-9_-]+)/g,
      (m, skip, slash, user) => skip ? skip : `[u/${user}](/user/${user})`);
}

const _xlateCache = new Map();
export async function xlateText(text) {
  if (!text?.trim()) return null;
  const key = text.trim().slice(0, 1000);
  if (_xlateCache.has(key)) return _xlateCache.get(key);
  const r = await fetch(`https://api.mymemory.translated.net/get?q=${encodeURIComponent(key)}&langpair=autodetect|en`);
  const d = await r.json();
  const detected = (d.matches || []).find(m => m['detected-language'])?.['detected-language'] || '';
  const result = { detected, translated: d.responseData?.translatedText || '' };
  _xlateCache.set(key, result);
  return result;
}

export function renderMd(text) {
  if (!text) return '';
  const processed = linkifyReddit(text).replace(/>!([\s\S]*?)(?:!<|$)/g, (_, inner) =>
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
  if (bodyEl && origBody.trim()) {
    bodyRes = await xlateText(origBody);
    if (bodyRes?.translated && bodyRes.translated !== origBody)
      bodyEl.innerHTML = renderMd(bodyRes.translated);
  }

  const bar = document.createElement('div');
  bar.className = 'xlate-bar';
  bar.innerHTML = `<span class="xlate-label">Translated from ${titleRes.detected}</span><button class="xlate-btn">View original</button>`;
  titleEl.after(bar);

  let showingTranslation = true;
  bar.querySelector('.xlate-btn').addEventListener('click', () => {
    showingTranslation = !showingTranslation;
    if (showingTranslation) {
      titleEl.textContent = titleRes.translated;
      if (bodyEl && bodyRes?.translated && bodyRes.translated !== origBody)
        bodyEl.innerHTML = renderMd(bodyRes.translated);
      bar.querySelector('.xlate-btn').textContent = 'View original';
    } else {
      titleEl.textContent = origTitle;
      if (bodyEl) bodyEl.innerHTML = renderMd(origBody);
      bar.querySelector('.xlate-btn').textContent = 'View translated';
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
    ? `<div class="xp-excerpt md">${renderMd(orig.selftext)}</div>` : '';
  return `<div class="crosspost-embed">
    <div class="crosspost-embed-header">↪ crossposted from <a href="/r/${sub}" data-nav="/r/${sub}">r/${sub}</a></div>
    <a class="crosspost-embed-title" href="${escHtml(nav)}" data-nav="${escHtml(nav)}">${escHtml(orig.title || '')}</a>
    ${mediaHtml}${excerptHtml}
  </div>`;
}

export function renderCrosspostFull(orig) { return renderCrosspostEmbed(orig, true); }

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
  tags += renderFlair(p, true);
  const titleClass = 'post-title'+(p.is_self?' is-italic':'');
  const domainHtml = !p.is_self && p.domain && !p.domain.endsWith('redd.it') ? `<a class="ext-link" href="${escHtml(p.url)}" target="_blank" rel="noopener"><svg width="9" height="9" viewBox="0 0 12 12" fill="none"><path d="M7 1h4m0 0v4m0-4L5.5 6.5M1 3h3.5M1 9h10M1 6h1.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>${escHtml(p.domain)}</a>` : '';
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
          <button class="comments-link" data-sub="${sub}" data-id="${id}">
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M14 8c0 3.314-2.686 6-6 6a6.03 6.03 0 0 1-2.83-.706L2 14l.706-3.17A6.03 6.03 0 0 1 2 8c0-3.314 2.686-6 6-6s6 2.686 6 6Z" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>
            ${fmtNum(p.num_comments)} comments
          </button>
          <button class="share-btn" data-share="/r/${sub}/comments/${id}" title="Copy link">
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><circle cx="12" cy="3" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="12" cy="13" r="1.5" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="8" r="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M10.5 3.87 5.5 7.13M5.5 8.87l5 3.26" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
          </button>
        </div>
      </div>`;

  const nsfwAttr = p.over_18 ? ' data-nsfw="1"' : '';

  if (p.crosspost_from) {
    return `
    <div class="post"${nsfwAttr} style="animation-delay:${delay}ms">
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
    const imgSrc = p.gallery?.[0]?.url ?? p.preview_img ?? null;
    let thumbHtml = '';
    if (imgSrc) {
      const thumbInner = `<img src="${escHtml(imgSrc)}" loading="lazy" alt="" onerror="this.parentElement.remove()">`;
      const thumbContent = p.over_18
        ? `<div class="nsfw-media-wrap nsfw-thumb-wrap"><div class="nsfw-veil" role="button" tabindex="0" onclick="event.preventDefault();this.parentElement.classList.add('revealed')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.parentElement.classList.add('revealed')}"><span class="nsfw-veil-label">nsfw</span></div><div class="nsfw-content">${thumbInner}</div></div>`
        : thumbInner;
      thumbHtml = `<a class="post-compact-thumb" href="${escHtml(p.url)}" target="_blank" rel="noopener">${thumbContent}</a>`;
    } else if (p.url && /^https?:\/\//.test(p.url)) {
      thumbHtml = `<a class="post-compact-thumb og-placeholder" href="${escHtml(p.url)}" target="_blank" rel="noopener" data-og-url="${escHtml(p.url)}" data-og-nsfw="${p.over_18 ? '1' : ''}"></a>`;
    }
    return `
    <div class="post post-compact"${nsfwAttr} style="animation-delay:${delay}ms">
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

  const excerptHtml = p.selftext ? `<div class="post-excerpt"><div class="md">${renderMd(p.selftext)}</div></div>` : '';
  return `
    <div class="post"${nsfwAttr} style="animation-delay:${delay}ms">
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
