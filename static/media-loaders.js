// Media resolved after render: redgifs videos, Imgur albums, and og:image previews
// and descriptions for link posts.
import { observeMedia, unobserveMedia, mediaDisposed, mediaSleeping } from './media-lifecycle.js';
import { settings } from './settings.js';
import { escHtml, evictMap, veilWrap, isLocalUrl } from './utils.js';
import { setupHls, trackVideoMute } from './media-video.js';
import { renderGallery, ZOOM_ATTRS } from './media-gallery.js';
import { dlOk, dlHref, dlFilename, dlFilenamePos, DL_ICON } from './media-download.js';

// Resolved URL cache shared across feed and postview — keyed by redgifs ID.
// Feed's batch fetch populates it; postview hits it instantly for the same IDs.
// Capped so a long scrolling session doesn't grow this unbounded.
const _rgCache = new Map();
const RG_CACHE_MAX = 500;

function _rgCacheSet(id, data) {
  if (_rgCache.has(id)) return;
  evictMap(_rgCache, RG_CACHE_MAX);
  _rgCache.set(id, Promise.resolve(data));
}

function _prefetchRedgifs(id) {
  if (_rgCache.has(id)) return;
  evictMap(_rgCache, RG_CACHE_MAX);
  _rgCache.set(id, fetch(`/api/redgifs/${id}`).then(r => r.ok ? r.json() : null).catch(() => null));
}

const _rgPrefetchObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting && !mediaDisposed(entry.target) && !mediaSleeping(entry.target)) _prefetchRedgifs(entry.target.dataset.rgid);
  });
}, { rootMargin: '400px' });

export function observeRedgifsPrefetch(container) {
  container.querySelectorAll('.redgifs-wrap[data-rgid]:not([data-rg-prefetch])').forEach(w => {
    w.dataset.rgPrefetch = '1';
    observeMedia(_rgPrefetchObserver, w);
    observeMedia(_rgBuildObserver, w);
  });
}

async function _buildRedgifsWrap(wrap) {
  const id = wrap.dataset.rgid;
  let data = _rgCache.get(id);
  if (!data) { _prefetchRedgifs(id); data = _rgCache.get(id); }
  data = (await data) ?? null;
  if (mediaDisposed(wrap)) return;
  if (!data || (!data.hd && !data.sd)) {
    const fbHls = wrap.dataset.fbHls, fbSrc = wrap.dataset.fbSrc;
    if (fbHls || fbSrc) {
      wrap.dataset.hls = fbHls || '';
      wrap.dataset.src = fbSrc || '';
      wrap.innerHTML = `<div class="rg-fallback-badge" title="Original video was removed from RedGifs — playing Reddit's mirrored copy, which has no audio">fallback · no audio</div><video controls preload="metadata" playsinline muted></video>`;
      setupHls(wrap.querySelector('video'), fbHls, fbSrc, null);
      trackVideoMute(wrap.querySelector('video'));
    } else {
      wrap.innerHTML = `<div class="rg-error">Could not load video</div>`;
    }
    return;
  }
  const videoSrc = data.hd || data.sd;
  const rgFname = videoSrc.split('/').pop().split('?')[0] || 'video.mp4';
  wrap.innerHTML = `<video controls playsinline preload="metadata" muted src="${escHtml(videoSrc)}"></video>`;
  trackVideoMute(wrap.querySelector('video'));
  // Activate the pv-meta placeholder if present
  const placeholder = document.querySelector(`[data-rg-dl="${CSS.escape(id)}"]`);
  if (placeholder) {
    const a = document.createElement('a');
    a.className = 'share-btn';
    a.href = videoSrc;
    a.download = rgFname;
    a.title = 'Download video';
    a.innerHTML = `${DL_ICON} download`;
    placeholder.replaceWith(a);
  }
}

// Batches wraps that become near-visible within the same tick into one call to
// /api/redgifs/batch (e.g. a page of feed cards all mounting at once), while wraps
// that only reach the viewport later via scrolling are built individually against
// the (likely already-prefetched) per-id cache. Building only fires near the
// viewport instead of for the whole container up front — a comment thread with
// dozens of gif replies no longer creates a <video> for every single one on load.
let _rgBuildQueue = [];
let _rgBuildScheduled = false;
function _flushRgBuildQueue() {
  const wraps = _rgBuildQueue;
  _rgBuildQueue = [];
  _rgBuildScheduled = false;
  (async () => {
    const coldIds = [...new Set(wraps.map(w => w.dataset.rgid).filter(id => !_rgCache.has(id)))];
    if (coldIds.length > 1) {
      try {
        const res = await fetch(`/api/redgifs/batch?ids=${coldIds.join(',')}`);
        if (res.ok) {
          const batchData = await res.json();
          Object.entries(batchData).forEach(([id, data]) => _rgCacheSet(id, data));
        }
      } catch {}
    }
    wraps.forEach(_buildRedgifsWrap);
  })();
}

const _rgBuildObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (!entry.isIntersecting || mediaDisposed(entry.target) || mediaSleeping(entry.target)) return;
    const wrap = entry.target;
    if (wrap.dataset.rgInit) return;
    wrap.dataset.rgInit = '1';
    unobserveMedia(_rgBuildObserver, wrap);
    unobserveMedia(_rgPrefetchObserver, wrap);
    _rgBuildQueue.push(wrap);
    if (!_rgBuildScheduled) {
      _rgBuildScheduled = true;
      setTimeout(_flushRgBuildQueue, 0);
    }
  });
}, { rootMargin: '200px' });

export async function initImgurAlbums(container) {
  const wraps = [...container.querySelectorAll('.imgur-album-wrap[data-iaid]:not([data-ia-init])')];
  await Promise.all(wraps.map(async wrap => {
    wrap.dataset.iaInit = '1';
    const id = wrap.dataset.iaid;
    try {
      const res = await fetch(`/api/imgur/album/${encodeURIComponent(id)}`);
      const data = await res.json();
      if (mediaDisposed(wrap)) return;
      if (!res.ok || !data.images?.length) throw new Error(data.error || 'no images');
      const imgs = data.images.map(img => ({url: img.url, width: img.width, height: img.height, caption: img.description || ''}));
      if (settings.linkExternalMedia && imgs.some(img => !isLocalUrl(img.url))) throw new Error('unproxied');
      const newHtml = imgs.length === 1
        ? `<div class="post-media"><img src="${escHtml(imgs[0].url)}"${wrap.classList.contains('pv-media') ? ZOOM_ATTRS : ''} loading="lazy" alt="${escHtml(imgs[0].caption)}"></div>`
        : renderGallery(imgs, wrap.classList.contains('pv-media'));
      wrap.insertAdjacentHTML('afterend', newHtml);
      wrap.remove();
      // Activate pv-meta placeholder if present
      const placeholder = document.querySelector(`[data-imgur-dl="${CSS.escape(id)}"]`);
      if (placeholder) {
        if (dlOk(imgs[0].url)) {
          const fname = imgs.length === 1 ? dlFilename(imgs[0].url) : dlFilenamePos(imgs[0].url, 1);
          const a = document.createElement('a');
          a.className = imgs.length === 1 ? 'share-btn' : 'share-btn pv-dl-gallery';
          a.href = dlHref(imgs[0].url, fname);
          a.download = fname;
          a.title = imgs.length === 1 ? 'Download image' : 'Download current image';
          a.innerHTML = `${DL_ICON} download`;
          placeholder.replaceWith(a);
        } else {
          placeholder.remove();
        }
      }
    } catch {
      if (mediaDisposed(wrap)) return;
      const cls = escHtml(wrap.classList.contains('pv-media') ? 'pv-media' : 'post-video');
      wrap.insertAdjacentHTML('afterend', settings.linkExternalMedia
        ? `<div class="${cls} minimal-media"><a class="minimal-media-link" href="https://imgur.com/a/${escHtml(id)}" target="_blank" rel="noopener noreferrer">Imgur album ↗</a></div>`
        : `<div class="${cls}"><iframe src="https://imgur.com/a/${escHtml(id)}/embed?pub=true" allowfullscreen loading="lazy" scrolling="no"></iframe></div>`);
      wrap.remove();
      document.querySelector(`[data-imgur-dl="${CSS.escape(id)}"]`)?.remove();
    }
  }));
}

// Dedupes og-image/description lookups: og-placeholder (image) and pv-article-desc
// (subtitle) can both want the same post URL, so share one fetch and one cached result.
const _ogFetchCache = new Map();
const OG_FETCH_CACHE_MAX = 500;
function fetchOg(url) {
  let p = _ogFetchCache.get(url);
  if (!p) {
    evictMap(_ogFetchCache, OG_FETCH_CACHE_MAX);
    p = fetch(`/api/og-image?url=${encodeURIComponent(url)}`).then(r => r.json());
    _ogFetchCache.set(url, p);
  }
  return p;
}

export function initOgImages(container) {
  container.querySelectorAll('.og-placeholder[data-og-url]:not([data-og-init])').forEach(wrap => {
    wrap.dataset.ogInit = '1';
    const url = wrap.dataset.ogUrl;
    fetchOg(url)
      .then(d => {
        if (mediaDisposed(wrap)) return;
        if (!d.url || (settings.linkExternalMedia && !isLocalUrl(d.url))) { wrap.remove(); return; }
        if (wrap.classList.contains('post-compact-thumb')) {
          const img = document.createElement('img');
          img.src = d.url;
          img.loading = 'lazy';
          img.alt = '';
          img.onerror = () => wrap.remove();
          if (wrap.dataset.ogNsfw) {
            wrap.innerHTML = veilWrap('nsfw', '', 'thumb');
            wrap.querySelector('.nsfw-content').appendChild(img);
          } else {
            wrap.appendChild(img);
          }
        } else {
          const cls = wrap.classList.contains('pv-media') ? 'pv-media' : 'post-media';
          const zoom = cls === 'pv-media' ? ZOOM_ATTRS : '';
          wrap.insertAdjacentHTML('afterend', `<div class="${cls}"><img src="${escHtml(d.url)}"${zoom} loading="lazy" alt="" data-onerror="no-media"></div>`);
          wrap.remove();
        }
      })
      .catch(() => { wrap.remove(); });
  });
}

export function initOgDescriptions(container) {
  if (settings.layout === 'minimal') return;
  container.querySelectorAll('.pv-article-desc[data-og-url]:not([data-og-init])').forEach(el => {
    el.dataset.ogInit = '1';
    const url = el.dataset.ogUrl;
    fetchOg(url)
      .then(d => {
        if (mediaDisposed(el)) return;
        if (!d.description) { el.remove(); return; }
        el.textContent = d.description;
      })
      .catch(() => { el.remove(); });
  });
}
