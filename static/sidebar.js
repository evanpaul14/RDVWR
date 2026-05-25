import { escHtml } from './utils.js';
import { renderMd } from './render.js';

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
