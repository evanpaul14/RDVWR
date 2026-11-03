import { TOUCH_MOVE_THRESHOLD, appPath, stripBase } from './utils.js';
import { navigate, navigateOrOpen } from './nav.js';

// ── Link interception ─────────────────────────────────────────────────────────
function interceptNavLink(a, e) {
  if (a.getAttribute('data-back')) { e.preventDefault(); history.back(); return true; }
  const datanav = a.getAttribute('data-nav');
  if (datanav) { e.preventDefault(); navigateOrOpen(datanav, e); return true; }

  const href = a.getAttribute('href') || '';
  if (!href || href.startsWith('#') || href.startsWith('javascript:') ||
      href.startsWith('mailto:') || href.startsWith('tel:')) return false;

  const redditLive = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/live\/([A-Za-z0-9_-]+)/);
  if (redditLive) { e.preventDefault(); navigateOrOpen(`/live/${redditLive[1]}`, e); return true; }
  const redditPost = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/r\/([^\/]+)\/comments\/([^\/?\s#]+)(?:\/[^\/?\s#]*\/([a-z0-9]+))?/i);
  if (redditPost) { e.preventDefault(); navigateOrOpen(`/r/${redditPost[1]}/comments/${redditPost[2]}${redditPost[3] ? '/comment/' + redditPost[3] : ''}`, e); return true; }
  const redditWiki = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/r\/([^\/]+)\/wiki(?:\/([^\s#?]*))?/);
  if (redditWiki) { e.preventDefault(); navigateOrOpen(`/r/${redditWiki[1]}/wiki/${redditWiki[2]||'index'}`, e); return true; }
  const redditSub  = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/r\/([^\/?\s#]+)(\/[^?\s#]*)?/);
  if (redditSub) {
    const extra = redditSub[2] || '';
    if (!extra || /^\/(hot|new|top|rising|controversial|best|gilded)?\/?$/.test(extra)) {
      e.preventDefault(); navigateOrOpen(`/r/${redditSub[1]}`, e); return true;
    }
    // Unrecognized path (e.g. share link /s/token) — resolve the redirect then navigate
    e.preventDefault();
    fetch(`/api/resolve?url=${encodeURIComponent(href)}`)
      .then(r => r.json())
      .then(data => {
        if (!data.url) return;
        const post = data.url.match(/\/r\/([^\/]+)\/comments\/([^\/?\s#]+)(?:\/[^\/?\s#]*\/([a-z0-9]+))?/i);
        if (post) { navigate(`/r/${post[1]}/comments/${post[2]}${post[3] ? '/comment/' + post[3] : ''}`); return; }
        const user = data.url.match(/\/u(?:ser)?\/([^\/?\s#]+)/);
        if (user) { navigate(`/user/${user[1]}`); return; }
        const sub = data.url.match(/\/r\/([^\/?\s#]+)/);
        if (sub) { navigate(`/r/${sub[1]}`); return; }
        window.open(data.url, '_blank');
      })
      .catch(() => window.open(href, '_blank'));
    return true;
  }
  const redditUser = href.match(/(?:https?:\/\/(?:www\.|old\.|new\.|np\.)?reddit\.com)\/u(?:ser)?\/([^\/?\s#]+)/);
  if (redditUser) { e.preventDefault(); navigateOrOpen(`/user/${redditUser[1]}`, e); return true; }
  try {
    const url = new URL(href, location.origin);
    if (url.origin !== location.origin) return false;
    e.preventDefault();
    navigateOrOpen(stripBase(url.pathname) + url.search, e);
    return true;
  } catch { return false; }
}

// Long-press on post card → open in new tab (mobile)
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
    window.open(appPath(titleLink.dataset.nav), '_blank');
  }, 550);
}, { passive: true });
document.addEventListener('touchmove', e => {
  if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
}, { passive: true });
document.addEventListener('touchend', e => {
  if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
}, { passive: true });

// iOS PWA: intercept in-app links on touchend
let _touchStartX = 0, _touchStartY = 0, _navFromTouch = false;
document.addEventListener('touchstart', e => {
  _touchStartX = e.touches[0].clientX;
  _touchStartY = e.touches[0].clientY;
  _navFromTouch = false;
}, { passive: true });
document.addEventListener('touchend', e => {
  // Leave edge touches alone so the browser's back/forward swipe gesture can fire
  if (_touchStartX < 20 || _touchStartX > window.innerWidth - 20) return;
  const dx = Math.abs(e.changedTouches[0].clientX - _touchStartX);
  const dy = Math.abs(e.changedTouches[0].clientY - _touchStartY);
  if (dx > TOUCH_MOVE_THRESHOLD || dy > TOUCH_MOVE_THRESHOLD) return;
  if (e.target.tagName === 'IMG' && e.target.closest('.md, .pv-media, .post-media')) return;
  if (e.target.closest('.spoiler:not(.revealed)')) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank') return;
  if (a.classList.contains('thumb-lightbox') && a.hasAttribute('data-lightbox')) return;
  if (interceptNavLink(a, e)) _navFromTouch = true;
}, { passive: false });

// Capture-phase click handler
document.addEventListener('click', e => {
  if (_navFromTouch) { _navFromTouch = false; return; }
  if (e.target.tagName === 'IMG' && e.target.closest('.md, .pv-media, .post-media')) return;
  const unrevealedSpoiler = e.target.closest('.spoiler:not(.revealed)');
  if (unrevealedSpoiler) { e.preventDefault(); e.stopPropagation(); unrevealedSpoiler.classList.add('revealed'); return; }
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank' || a.hasAttribute('download')) return;
  if (a.classList.contains('thumb-lightbox') && a.hasAttribute('data-lightbox')) return;
  interceptNavLink(a, e);
}, true);

// Spoiler/nsfw veils (veilWrap). Capture phase so the reveal + preventDefault land before
// bubble-phase card/keyboard handlers, matching the old inline-handler ordering.
document.addEventListener('click', e => {
  const veil = e.target.closest('.spoiler-veil, .nsfw-veil');
  if (!veil) return;
  e.preventDefault();
  veil.parentElement.classList.add('revealed');
}, true);
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const veil = e.target.closest?.('.spoiler-veil, .nsfw-veil');
  if (!veil) return;
  e.preventDefault();
  veil.parentElement.classList.add('revealed');
}, true);

// Middle-click
document.addEventListener('auxclick', e => {
  if (e.button !== 1) return;
  if (e.target.closest('.spoiler:not(.revealed)')) return;
  const a = e.target.closest('a[data-nav], a[href]');
  if (!a || a.getAttribute('target') === '_blank' || a.hasAttribute('download')) return;
  interceptNavLink(a, e);
}, true);
