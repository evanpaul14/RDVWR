import { settings } from './settings.js';

export const SKELETON_COUNT        = 5;
export const ANIM_DELAY_STEP       = 40;
export const ANIM_DELAY_MAX        = 400;
export const AUTOCOMPLETE_DEBOUNCE = 280;
export const TOUCH_MOVE_THRESHOLD  = 10;
export const GALLERY_SWIPE_MIN     = 40;

// Opens a real reddit.com URL in a new tab. Tags the URL so the rdvwr browser
// extension's redirect-to-rdvwr logic (background.js) knows not to bounce
// this navigation straight back — otherwise clicking "open in reddit" from
// inside the app would just redirect back to the app. Uses a URL fragment
// (not a query param) so the tag stays client-side and is never sent to Reddit.
export function openOnReddit(href) {
  let target = href;
  try {
    const u = new URL(href);
    u.hash = 'rdvwr_bypass';
    target = u.toString();
  } catch {}
  window.open(target, '_blank', 'noopener');
}

export function fmtNum(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return String(n);
}
export function timeAgo(utc) {
  const s = Math.floor(Date.now()/1000) - utc;
  if (s < 60)       return `${s}s`;
  if (s < 3600)     return `${Math.floor(s/60)}m`;
  if (s < 86400)    return `${Math.floor(s/3600)}h`;
  if (s < 2592000)  return `${Math.floor(s/86400)}d`;
  if (s < 31536000) return `${Math.floor(s/2592000)}mo`;
  return `${Math.floor(s/31536000)}y`;
}
export function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
export function fmtDate(utc) {
  return new Date(utc*1000).toLocaleDateString(undefined, {year:'numeric',month:'short',day:'numeric'});
}
export function fmtDateTime(utc) {
  return new Date(utc*1000).toLocaleString(undefined, {year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
}
export function setActiveButton(container, dataAttr, activeVal) {
  container.querySelectorAll(`[data-${dataAttr}]`).forEach(b =>
    b.classList.toggle('active', b.dataset[dataAttr] === activeVal)
  );
}

export function isUsableBg(hex) {
  if (!hex || hex === 'transparent') return false;
  const m = hex.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (!m) return false;
  const lum = 0.299*parseInt(m[1],16) + 0.587*parseInt(m[2],16) + 0.114*parseInt(m[3],16);
  return lum < 180;
}

export function renderFlair(p, clickable=false) {
  if (!p.flair && !p.flair_richtext?.length) return '';
  let inner = '';
  if (p.flair_type === 'richtext' && p.flair_richtext?.length) {
    inner = p.flair_richtext.map(part => {
      if (part.e === 'text')  return escHtml(part.t || '');
      if (part.e === 'emoji') {
        const label = (part.a || '').replace(/^:|:$/g, '');
        return settings.layout === 'minimal' && label ? escHtml(label) : `<img class="flair-emoji" src="${escHtml(part.u)}" alt="${escHtml(part.a||'')}" loading="lazy">`;
      }
      return '';
    }).join(settings.layout === 'minimal' ? ' ' : '');
  } else {
    inner = escHtml(p.flair);
  }
  const bg = isUsableBg(p.flair_bg) ? p.flair_bg : '';
  const style = bg ? ` style="background:${escHtml(bg)};color:${p.flair_tc==='light'?'#fff':'#1a1a1a'}"` : '';
  const cls = clickable && p.flair ? ' flair-clickable' : '';
  const dataAttr = clickable && p.flair ? ` data-flair="${escHtml(p.flair)}" data-sub="${escHtml(p.subreddit)}"` : '';
  return `<span class="flair${cls}"${style}${dataAttr}>${inner}</span>`;
}

export function renderAwards(awards) {
  if (!awards?.length) return '';
  if (settings.layout === 'minimal') {
    const total = awards.reduce((n, a) => n + (a.count > 1 ? a.count : 1), 0);
    return `<span class="awards" title="${awards.map(a => escHtml(a.name)).join(', ')}">🏅${total}</span>`;
  }
  return `<span class="awards">${awards.map(a =>
    `<span class="award-item" title="${escHtml(a.name)}${a.count > 1 ? ' ×'+a.count : ''}">` +
    `<img src="${escHtml(a.icon)}" alt="${escHtml(a.name)}" loading="lazy" width="16" height="16">` +
    (a.count > 1 ? `<span class="award-count">${a.count}</span>` : '') +
    `</span>`
  ).join('')}</span>`;
}

export function renderAuthorFlair(c) {
  const hasRichtext = c.author_flair_type === 'richtext' && c.author_flair_richtext?.length;
  if (!hasRichtext && !c.author_flair_text) return '';
  let inner = '';
  if (hasRichtext) {
    inner = c.author_flair_richtext.map(part => {
      if (part.e === 'text')  return escHtml(part.t || '');
      if (part.e === 'emoji') {
        const label = (part.a || '').replace(/^:|:$/g, '');
        return settings.layout === 'minimal' && label ? escHtml(label) : `<img class="author-flair-emoji" src="${escHtml(part.u)}" alt="${escHtml(part.a||'')}" loading="lazy">`;
      }
      return '';
    }).join(settings.layout === 'minimal' ? ' ' : '');
  } else {
    inner = escHtml(c.author_flair_text);
  }
  if (!inner.trim() && !c.author_flair_richtext?.some(p => p.e === 'emoji')) return '';
  const bg = isUsableBg(c.author_flair_bg) ? c.author_flair_bg : '';
  const style = bg ? ` style="background:${escHtml(bg)};color:${c.author_flair_tc==='light'?'#fff':'#1a1a1a'}"` : '';
  return `<span class="author-flair"${style}>${inner}</span>`;
}

export function errState(msg, retryTarget, sub='') {
  const subHtml = sub ? `<div class="state-sub">${sub}</div>` : '';
  return `<div class="state"><div class="state-icon">⚠</div><div class="state-title">${msg}</div>${subHtml}<button class="state-retry-btn" data-retry="${retryTarget}">Try again</button></div>`;
}

export function buildTimeFilterHtml(selected) {
  return `<div class="time-filter-wrap"><select class="time-filter" id="time-filter">
    <option value="all"${selected==='all'?' selected':''}>All time</option>
    <option value="year"${selected==='year'?' selected':''}>Past year</option>
    <option value="month"${selected==='month'?' selected':''}>Past month</option>
    <option value="week"${selected==='week'?' selected':''}>Past week</option>
    <option value="day"${selected==='day'?' selected':''}>Today</option>
  </select></div>`;
}

export function evictMap(map, max) {
  if (map.size < max) return;
  const n = Math.ceil(max / 5);
  const it = map.keys();
  for (let i = 0; i < n; i++) {
    const { value, done } = it.next();
    if (done) break;
    map.delete(value);
  }
}

export function renderPoll(poll) {
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
