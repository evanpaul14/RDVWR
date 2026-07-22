import { escHtml, fmtNum, timeAgo } from './utils.js';
import { renderMd } from './render.js';

function renderWidget(w) {
  const title = w.name ? `<div class="sidebar-section-title">${escHtml(w.name)}</div>` : '';
  if (w.kind === 'community-list') {
    const items = w.items.map(c => `<a class="sidebar-community" href="/r/${escHtml(c.name)}" data-nav="/r/${escHtml(c.name)}">`+
      (c.icon ? `<img class="sidebar-community-icon" src="${escHtml(c.icon)}" alt="" loading="lazy">` : `<span class="sidebar-community-icon sidebar-community-icon--blank"></span>`)+
      `<span class="sidebar-community-name">r/${escHtml(c.name)}</span>`+
      (c.subscribers ? `<span class="sidebar-community-subs">${fmtNum(c.subscribers)}</span>` : '')+
      `</a>`).join('');
    return `<div class="sidebar-section">${title}<div class="sidebar-community-list">${items}</div></div>`;
  }
  if (w.kind === 'calendar') {
    const items = w.events.map(ev => `<li class="sidebar-event">
      <span class="sidebar-event-title">${escHtml(ev.title)}</span>
      ${ev.startTime ? `<span class="sidebar-event-time">${ev.allDay ? '' : timeAgo(ev.startTime)}</span>` : ''}
    </li>`).join('');
    return `<div class="sidebar-section">${title}<ul class="sidebar-events">${items}</ul></div>`;
  }
  if (w.kind === 'image') {
    const items = w.images.map(im => {
      const img = `<img class="sidebar-widget-img" src="${escHtml(im.url)}" alt="" loading="lazy">`;
      return im.linkUrl ? `<a href="${escHtml(im.linkUrl)}" target="_blank" rel="noopener noreferrer">${img}</a>` : img;
    }).join('');
    return `<div class="sidebar-section">${title}${items}</div>`;
  }
  if (w.kind === 'textarea') {
    return `<div class="sidebar-section">${title}<div class="sidebar-desc md">${renderMd(w.text)}</div></div>`;
  }
  if (w.kind === 'button') {
    const items = w.buttons.map(b =>
      `<a class="sidebar-widget-btn" href="${escHtml(b.url)}" target="_blank" rel="noopener noreferrer"${b.color ? ` style="border-color:${escHtml(b.color)}"` : ''}>${escHtml(b.text)}</a>`
    ).join('');
    return `<div class="sidebar-section">${title}<div class="sidebar-widget-btns">${items}</div></div>`;
  }
  if (w.kind === 'menu') {
    const items = w.links.map(l => `<a class="sidebar-menu-link" href="${escHtml(l.url)}" target="_blank" rel="noopener noreferrer">${escHtml(l.text)}</a>`).join('');
    return `<div class="sidebar-section">${title}<div class="sidebar-menu">${items}</div></div>`;
  }
  return '';
}

const sidebarPanel = document.getElementById('sidebar-panel');
const sidebarInner = document.getElementById('sidebar-inner');

let sidebarOpen   = false;
let sidebarSub    = '';
let _sidebarCache = new Map();
const SIDEBAR_CACHE_TTL = 5 * 60 * 1000;

export function closeSidebar() {
  sidebarOpen = false;
  sidebarPanel.classList.remove('open');
  const btn = document.getElementById('sidebar-toggle-btn');
  if (btn) { btn.classList.remove('active'); btn.setAttribute('aria-expanded', 'false'); }
}

export async function toggleSidebar(sub) {
  if (sidebarOpen && sidebarSub === sub) { closeSidebar(); return; }
  sidebarSub = sub;
  sidebarOpen = true;
  sidebarPanel.classList.add('open');
  const btn = document.getElementById('sidebar-toggle-btn');
  if (btn) { btn.classList.add('active'); btn.setAttribute('aria-expanded', 'true'); }

  const cached = _sidebarCache.get(sub);
  if (cached && Date.now() - cached.ts < SIDEBAR_CACHE_TTL) {
    sidebarInner.innerHTML = cached.html;
    return;
  }

  sidebarInner.innerHTML = '<div style="padding:10px 0;font-family:var(--mono);font-size:11px;color:var(--tx3)">Loading…</div>';

  try {
    const [aboutRes, rulesRes, modsRes, widgetsRes] = await Promise.all([
      fetch(`/api/r/${encodeURIComponent(sub)}/about`),
      fetch(`/api/r/${encodeURIComponent(sub)}/rules`),
      fetch(`/api/r/${encodeURIComponent(sub)}/about/moderators`),
      fetch(`/api/r/${encodeURIComponent(sub)}/widgets`),
    ]);
    const about = aboutRes.ok ? await aboutRes.json() : {};
    const rulesData = rulesRes.ok ? await rulesRes.json() : {rules:[]};
    const modsData = modsRes.ok ? await modsRes.json() : {moderators:[]};
    const widgetsData = widgetsRes.ok ? await widgetsRes.json() : {widgets:[]};

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
    if (widgetsData.widgets?.length) {
      html += widgetsData.widgets.map(renderWidget).join('');
    }
    if (modsData.moderators?.length) {
      const visible = modsData.moderators.slice(0, 12);
      const modList = visible.map(m =>
        `<a class="sidebar-mod-name" href="/user/${escHtml(m.name)}" data-nav="/user/${escHtml(m.name)}">u/${escHtml(m.name)}</a>`
      ).join('');
      const more = modsData.moderators.length > 12
        ? `<span class="sidebar-mod-more">+${modsData.moderators.length - 12} more</span>` : '';
      html += `<div class="sidebar-section">
        <div class="sidebar-section-title">Moderators</div>
        <div class="sidebar-mods">${modList}${more}</div>
      </div>`;
    }
    if (!html) html = '<div style="font-family:var(--mono);font-size:11px;color:var(--tx3)">No sidebar content.</div>';
    _sidebarCache.set(sub, { html, ts: Date.now() });
    sidebarInner.innerHTML = html;
  } catch {
    sidebarInner.innerHTML = '<div style="font-family:var(--mono);font-size:11px;color:var(--tx3)">Failed to load sidebar.</div>';
  }
}
