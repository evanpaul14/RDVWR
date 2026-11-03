import { settings, personalizedHomeMode } from './settings.js';
import { navigate, onRoute } from './nav.js';
import { personalizedHomeActive } from './home-mode.js';

// Feeds menu: a plain "saved" link, or a saved/popular dropdown when cookies enable popular
const feedsBtn      = document.getElementById('feeds-btn');
const feedsDropdown = document.getElementById('feeds-dropdown');
export function setFeedsMenuOpen(open) {
  feedsDropdown.hidden = !open;
  feedsBtn.setAttribute('aria-expanded', String(open));
}
feedsBtn.addEventListener('click', e => {
  e.stopPropagation();
  if (!personalizedHomeMode()) { navigate('/saved'); return; }
  setFeedsMenuOpen(feedsDropdown.hidden);
});
feedsDropdown.addEventListener('click', e => {
  const item = e.target.closest('.feeds-item[data-feed]');
  if (!item) return;
  setFeedsMenuOpen(false);
  navigate(item.dataset.feed);
});
document.addEventListener('click', e => {
  if (!feedsDropdown.hidden && !e.target.closest('#feeds-menu')) setFeedsMenuOpen(false);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !feedsDropdown.hidden) setFeedsMenuOpen(false);
});

// Subscribed-feed subreddit list: close on outside click, Escape, or picking a subreddit
document.addEventListener('click', e => {
  const open = document.querySelector('.ctx-subs[open]');
  if (open && (!open.contains(e.target) || e.target.closest('.ctx-subs-item'))) open.open = false;
});
document.addEventListener('keydown', e => {
  const open = document.querySelector('.ctx-subs[open]');
  if (e.key === 'Escape' && open) open.open = false;
});

// ── Feeds button mode ─────────────────────────────────────────────────────────
export function updateFeedsBtn() {
  const menu = personalizedHomeActive();
  document.getElementById('feeds-menu').classList.toggle('has-menu', menu);
  feedsBtn.textContent = menu ? 'feeds ▾' : 'saved';
  feedsBtn.setAttribute('aria-label', menu ? 'Feeds' : 'Saved posts');
  if (menu) feedsBtn.setAttribute('aria-haspopup', 'menu');
  else { feedsBtn.removeAttribute('aria-haspopup'); setFeedsMenuOpen(false); }
  // Popular and the separate subscribed feed only exist alongside a personalized home feed.
  feedsDropdown.querySelector('.feeds-item[data-feed="/r/popular"]').hidden = !menu || settings.homeFeed === 'popular';
  // No separate subscribed entry when it's already what Home shows.
  feedsDropdown.querySelector('.feeds-item[data-feed="/subscribed"]').hidden = !menu || settings.homeFeed === 'subscribed';
}

function updateFeedsActive(route) {
  const current = route.type === 'saved' ? '/saved'
    : route.type === 'subscribed' ? '/subscribed'
    : route.type === 'sub' && route.sub.toLowerCase() === 'popular' ? '/r/popular'
    : null;
  feedsDropdown.querySelectorAll('.feeds-item').forEach(item => {
    const on = item.dataset.feed === current;
    item.classList.toggle('active', on);
    if (on) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  });
}

onRoute(updateFeedsActive);
