import { state } from './state.js';
import { escHtml, renderPoll, GALLERY_SWIPE_MIN } from './utils.js';

function _trackVideoMute(v) {
  if (v.dataset.muteTracked) return;
  v.dataset.muteTracked = '1';
  v.muted = state.userPrefersMuted;
  v.addEventListener('volumechange', () => {
    const nowMuted = v.muted || v.volume === 0;
    if (nowMuted !== state.userPrefersMuted) {
      state.userPrefersMuted = nowMuted;
      localStorage.setItem('mutePreference', nowMuted ? 'muted' : 'unmuted');
      document.querySelectorAll('video[data-mute-tracked]').forEach(other => {
        if (other !== v) other.muted = nowMuted;
      });
    }
  });
}

const _DL_HOSTS = new Set(['v.redd.it','i.redd.it','preview.redd.it','external-preview.redd.it','i.imgur.com']);
function _dlOk(url) {
  if (!url) return false;
  try { return _DL_HOSTS.has(new URL(url).hostname); } catch { return false; }
}
function _dlHref(url, filename) {
  return `/api/download?url=${encodeURIComponent(url)}&filename=${encodeURIComponent(filename)}`;
}
function _dlFilename(url) {
  try { return new URL(url).pathname.split('/').filter(Boolean).pop() || 'media'; }
  catch { return 'media'; }
}
function _dlFilenamePos(url, pos) {
  const name = _dlFilename(url);
  const dot = name.lastIndexOf('.');
  return dot > 0 ? `${name.slice(0, dot)}-${pos}${name.slice(dot)}` : `${name}-${pos}`;
}
const _DL_ICON = `<svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M8 2v8M5 7l3 3 3-3M3 13h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

export function syncAudio(videoEl, audioSrc) {
  const audio = new Audio(audioSrc);
  audio.preload = 'none';
  videoEl.addEventListener('play',         () => { audio.currentTime = videoEl.currentTime; audio.play().catch(()=>{}); });
  videoEl.addEventListener('pause',        () => audio.pause());
  videoEl.addEventListener('seeked',       () => { audio.currentTime = videoEl.currentTime; });
  videoEl.addEventListener('volumechange', () => { audio.volume = videoEl.volume; audio.muted = videoEl.muted; });
}

let _hlsPromise = null;
function _ensureHls() {
  if (typeof Hls !== 'undefined') return Promise.resolve();
  if (_hlsPromise) return _hlsPromise;
  _hlsPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/hls.min.js';
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });
  return _hlsPromise;
}

export async function setupHls(videoEl, hlsUrl, fallback, audioSrc) {
  if (hlsUrl) {
    await _ensureHls();
  }
  if (hlsUrl && typeof Hls !== 'undefined' && Hls.isSupported()) {
    const hls = new Hls({ autoStartLoad: false, startLevel: 999 });
    hls.loadSource(hlsUrl); hls.attachMedia(videoEl);
    videoEl.addEventListener('play', () => hls.startLoad(), { once: true });
    hls.on(Hls.Events.MANIFEST_PARSED, (_ev, data) => {
      if (data.levels.length < 2) return;
      const wrap = videoEl.closest('[data-hls]');
      if (!wrap) return;
      const seen = new Map();
      data.levels.forEach((l, i) => {
        const key = l.height || `${Math.round(l.bitrate / 1000)}k`;
        const prev = seen.get(key);
        if (!prev || l.bitrate > prev.bitrate) seen.set(key, { idx: i, bitrate: l.bitrate, height: l.height });
      });
      const levels = [...seen.values()].map(l => ({
        idx: l.idx,
        label: l.height ? `${l.height}p` : `${Math.round(l.bitrate / 1000)}k`,
      }));
      const labelByIdx = new Map(levels.map(l => [l.idx, l.label]));
      const btn = document.createElement('button');
      btn.className = 'hls-quality-btn';
      btn.textContent = 'auto';
      btn.title = 'Video quality';
      const menu = document.createElement('div');
      menu.className = 'hls-quality-menu';
      menu.innerHTML = `<button class="hls-ql active" data-level="-1">Auto</button>` +
        levels.map(l => `<button class="hls-ql" data-level="${l.idx}">${l.label}</button>`).join('');
      wrap.append(btn, menu);
      const hideBtn = () => { if (!menu.classList.contains('open')) btn.style.opacity = ''; };
      wrap.addEventListener('mouseenter', () => { btn.style.opacity = '1'; }, { passive: true });
      wrap.addEventListener('mouseleave', hideBtn, { passive: true });
      btn.addEventListener('click', e => { e.stopPropagation(); menu.classList.toggle('open'); });
      document.addEventListener('click', () => { menu.classList.remove('open'); hideBtn(); }, { passive: true });
      menu.addEventListener('click', e => {
        const ql = e.target.closest('.hls-ql');
        if (!ql) return;
        const lvl = parseInt(ql.dataset.level, 10);
        hls.currentLevel = lvl;
        menu.querySelectorAll('.hls-ql').forEach(b => b.classList.toggle('active', b === ql));
        btn.textContent = lvl === -1 ? 'auto' : (labelByIdx.get(lvl) ?? 'auto');
        menu.classList.remove('open');
        hideBtn();
      });
      hls.on(Hls.Events.LEVEL_SWITCHED, (_ev2, d) => {
        if (hls.autoLevelEnabled) btn.textContent = `auto (${labelByIdx.get(d.level) ?? ''})`;
      });
    });
  } else if (hlsUrl && videoEl.canPlayType('application/vnd.apple.mpegurl')) {
    videoEl.src = hlsUrl;
  } else if (fallback) {
    videoEl.src = fallback;
  }
  if (audioSrc) syncAudio(videoEl, audioSrc);
}

const _gifObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    const v = entry.target;
    if (entry.isIntersecting) {
      v.play().catch(() => {});
    } else {
      v.pause();
    }
  });
}, { threshold: 0.1 });

// Resolved URL cache shared across feed and postview — keyed by redgifs ID.
// Feed's batch fetch populates it; postview hits it instantly for the same IDs.
// Capped so a long scrolling session doesn't grow this unbounded.
const _rgCache = new Map();
const RG_CACHE_MAX = 500;

function _rgCacheEvictIfFull() {
  if (_rgCache.size < RG_CACHE_MAX) return;
  const evictCount = Math.ceil(RG_CACHE_MAX / 5);
  const it = _rgCache.keys();
  for (let i = 0; i < evictCount; i++) {
    const { value, done } = it.next();
    if (done) break;
    _rgCache.delete(value);
  }
}

function _rgCacheSet(id, data) {
  if (_rgCache.has(id)) return;
  _rgCacheEvictIfFull();
  _rgCache.set(id, Promise.resolve(data));
}

function _prefetchRedgifs(id) {
  if (_rgCache.has(id)) return;
  _rgCacheEvictIfFull();
  _rgCache.set(id, fetch(`/api/redgifs/${id}`).then(r => r.ok ? r.json() : null).catch(() => null));
}

const _rgPrefetchObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) _prefetchRedgifs(entry.target.dataset.rgid);
  });
}, { rootMargin: '400px' });

export function initGifVideos(container) {
  container.querySelectorAll('video[autoplay]:not([data-gif-obs])').forEach(v => {
    v.dataset.gifObs = '1';
    v.removeAttribute('autoplay');
    _gifObserver.observe(v);
  });
}

export function initVideos(container) {
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
  container.querySelectorAll('video').forEach(_trackVideoMute);
}

export function observeRedgifsPrefetch(container) {
  container.querySelectorAll('.redgifs-wrap[data-rgid]:not([data-rg-prefetch])').forEach(w => {
    w.dataset.rgPrefetch = '1';
    _rgPrefetchObserver.observe(w);
  });
}

export async function initRedgifs(container) {
  const wraps = [...container.querySelectorAll('.redgifs-wrap[data-rgid]:not([data-rg-init])')];
  if (!wraps.length) return;
  wraps.forEach(w => {
    w.dataset.rgInit = '1';
    _rgPrefetchObserver.unobserve(w);
  });
  const ids = wraps.map(w => w.dataset.rgid);
  const coldIds = ids.filter(id => !_rgCache.has(id));
  let batchData = {};
  if (coldIds.length) {
    try {
      const res = await fetch(`/api/redgifs/batch?ids=${coldIds.join(',')}`);
      if (res.ok) {
        batchData = await res.json();
        Object.entries(batchData).forEach(([id, data]) => _rgCacheSet(id, data));
      }
    } catch {}
  }
  await Promise.all(wraps.map(async wrap => {
    const id = wrap.dataset.rgid;
    let data = batchData[id] ?? await _rgCache.get(id) ?? null;
    if (!data) {
      try {
        const res = await fetch(`/api/redgifs/${id}`);
        data = res.ok ? await res.json() : null;
        if (data) _rgCacheSet(id, data);
      } catch { data = null; }
    }
    if (!data || (!data.hd && !data.sd)) {
      wrap.innerHTML = `<div class="rg-error">Could not load video</div>`;
      return;
    }
    const videoSrc = data.hd || data.sd;
    const rgFname = videoSrc.split('/').pop().split('?')[0] || 'video.mp4';
    wrap.innerHTML = `<video controls playsinline preload="metadata" muted src="${escHtml(videoSrc)}"></video>`;
    _trackVideoMute(wrap.querySelector('video'));
    // Activate the pv-meta placeholder if present
    const placeholder = document.querySelector(`[data-rg-dl="${CSS.escape(id)}"]`);
    if (placeholder) {
      const a = document.createElement('a');
      a.className = 'share-btn';
      a.href = videoSrc;
      a.download = rgFname;
      a.title = 'Download video';
      a.innerHTML = `${_DL_ICON} download`;
      placeholder.replaceWith(a);
    }
  }));
}

export function initMedia(container) {
  initVideos(container);
  observeRedgifsPrefetch(container);
  initRedgifs(container);
  initImgurAlbums(container);
  initOgImages(container);
  initOgDescriptions(container);
}

export async function initImgurAlbums(container) {
  const wraps = [...container.querySelectorAll('.imgur-album-wrap[data-iaid]:not([data-ia-init])')];
  await Promise.all(wraps.map(async wrap => {
    wrap.dataset.iaInit = '1';
    const id = wrap.dataset.iaid;
    try {
      const res = await fetch(`/api/imgur/album/${encodeURIComponent(id)}`);
      const data = await res.json();
      if (!res.ok || !data.images?.length) throw new Error(data.error || 'no images');
      const imgs = data.images.map(img => ({url: img.url, width: img.width, height: img.height, caption: img.description || ''}));
      const newHtml = imgs.length === 1
        ? `<div class="post-media"><img src="${escHtml(imgs[0].url)}" loading="lazy" alt="${escHtml(imgs[0].caption)}"></div>`
        : renderGallery(imgs);
      wrap.insertAdjacentHTML('afterend', newHtml);
      wrap.remove();
      // Activate pv-meta placeholder if present
      const placeholder = document.querySelector(`[data-imgur-dl="${CSS.escape(id)}"]`);
      if (placeholder) {
        if (_dlOk(imgs[0].url)) {
          const fname = imgs.length === 1 ? _dlFilename(imgs[0].url) : _dlFilenamePos(imgs[0].url, 1);
          const a = document.createElement('a');
          a.className = imgs.length === 1 ? 'share-btn' : 'share-btn pv-dl-gallery';
          a.href = _dlHref(imgs[0].url, fname);
          a.download = fname;
          a.title = imgs.length === 1 ? 'Download image' : 'Download current image';
          a.innerHTML = `${_DL_ICON} download`;
          placeholder.replaceWith(a);
        } else {
          placeholder.remove();
        }
      }
    } catch {
      wrap.insertAdjacentHTML('afterend', `<div class="${escHtml(wrap.classList.contains('pv-media') ? 'pv-media' : 'post-video')}"><iframe src="https://imgur.com/a/${escHtml(id)}/embed?pub=true" allowfullscreen loading="lazy" scrolling="no"></iframe></div>`);
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
    if (_ogFetchCache.size >= OG_FETCH_CACHE_MAX) {
      const evictCount = Math.ceil(OG_FETCH_CACHE_MAX / 5);
      const it = _ogFetchCache.keys();
      for (let i = 0; i < evictCount; i++) {
        const { value, done } = it.next();
        if (done) break;
        _ogFetchCache.delete(value);
      }
    }
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
        if (!d.url) { wrap.remove(); return; }
        if (wrap.classList.contains('post-compact-thumb')) {
          const img = document.createElement('img');
          img.src = d.url;
          img.loading = 'lazy';
          img.alt = '';
          img.onerror = () => wrap.remove();
          if (wrap.dataset.ogNsfw) {
            wrap.innerHTML = `<div class="nsfw-media-wrap nsfw-thumb-wrap"><div class="nsfw-veil" role="button" tabindex="0" onclick="event.preventDefault();this.parentElement.classList.add('revealed')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.parentElement.classList.add('revealed')}"><span class="nsfw-veil-label">nsfw</span></div><div class="nsfw-content"></div></div>`;
            wrap.querySelector('.nsfw-content').appendChild(img);
          } else {
            wrap.appendChild(img);
          }
        } else {
          const cls = wrap.classList.contains('pv-media') ? 'pv-media' : 'post-media';
          wrap.insertAdjacentHTML('afterend', `<div class="${cls}"><img src="${escHtml(d.url)}" loading="lazy" alt="" onerror="this.parentElement.classList.add('no-media')"></div>`);
          wrap.remove();
        }
      })
      .catch(() => { wrap.remove(); });
  });
}

export function initOgDescriptions(container) {
  container.querySelectorAll('.pv-article-desc[data-og-url]:not([data-og-init])').forEach(el => {
    el.dataset.ogInit = '1';
    const url = el.dataset.ogUrl;
    fetchOg(url)
      .then(d => {
        if (!d.description) { el.remove(); return; }
        el.textContent = d.description;
      })
      .catch(() => { el.remove(); });
  });
}

export function renderGallery(images) {
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
            <button class="gallery-btn gallery-prev" aria-label="Previous image" disabled>‹</button>
            <span class="gallery-counter">1 / ${images.length}</span>
            <button class="gallery-btn gallery-next" aria-label="Next image">›</button>
          </div>` : ''}
      </div>
      ${images[0].caption ? `<div class="gallery-caption">${escHtml(images[0].caption)}</div>` : ''}
      ${images.length > 1 ? `<div class="gallery-thumbs">${thumbsHtml}</div>` : ''}
    </div>`;
}

export function spoilerWrap(html) {
  return `<div class="spoiler-media-wrap"><div class="spoiler-veil" role="button" tabindex="0" onclick="this.parentElement.classList.add('revealed')" onkeydown="if(event.key==='Enter'||event.key===' '){this.parentElement.classList.add('revealed');event.preventDefault()}"><span class="spoiler-veil-label">spoiler — click to reveal</span></div><div class="spoiler-content">${html}</div></div>`;
}

export function nsfwWrap(html) {
  return `<div class="nsfw-media-wrap"><div class="nsfw-veil" role="button" tabindex="0" onclick="this.parentElement.classList.add('revealed')" onkeydown="if(event.key==='Enter'||event.key===' '){this.parentElement.classList.add('revealed');event.preventDefault()}"><span class="nsfw-veil-label">nsfw — click to reveal</span></div><div class="nsfw-content">${html}</div></div>`;
}

export function mediaHtml(p, full = false) {
  if (p.poll) return renderPoll(p.poll);
  if (p.is_devvit) {
    const imgHtml = p.preview_img
      ? `<img class="devvit-preview" src="${escHtml(p.preview_img)}" loading="lazy" alt="">`
      : '';
    const href = escHtml(p.devvit_url || p.permalink || '#');
    const permalink = escHtml(p.permalink || '');
    return `<div class="devvit-card${full ? ' devvit-card-full' : ''}" data-permalink="${permalink}">
      ${imgHtml}
      <div class="devvit-overlay">
        <span class="devvit-badge"><svg width="14" height="14" viewBox="0 0 20 20" fill="none"><rect x="2" y="2" width="16" height="16" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M7 10h6M10 7v6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg> Interactive App</span>
        <div class="devvit-btns">
          <button class="devvit-try-btn" title="Load the app in this page">Try App</button>
          <a class="devvit-open-btn" href="${href}" target="_blank" rel="noopener noreferrer">Open on Reddit ↗</a>
        </div>
      </div>
    </div>`;
  }
  const vc = full ? 'pv-media' : 'post-video';
  const ic = full ? 'pv-media' : 'post-media';
  const preload = full ? 'metadata' : 'none';
  let html = '';
  if (p.is_video) {
    html = `<div class="${vc}" data-hls="${escHtml(p.hls_url||'')}" data-src="${escHtml(p.video_url||'')}" data-audio="${escHtml(p.audio_url||'')}"`+(p.preview_img?` data-poster="${escHtml(p.preview_img)}"`:'')+`><video controls preload="${preload}" playsinline muted></video></div>`;
  } else if (p.youtube_id) {
    html = `<div class="${vc}"><iframe src="https://www.youtube-nocookie.com/embed/${escHtml(p.youtube_id)}" allowfullscreen loading="lazy"></iframe></div>`;
  } else if (p.tiktok_id) {
    html = `<div class="${vc} tiktok-wrap"><iframe src="https://www.tiktok.com/player/v1/${escHtml(p.tiktok_id)}?autoplay=0&rel=0" allowfullscreen loading="lazy" sandbox="allow-scripts allow-same-origin allow-popups"></iframe></div>`;
  } else if (p.redgifs_id) {
    html = `<div class="${vc} redgifs-wrap" data-rgid="${escHtml(p.redgifs_id)}"><div class="rg-loading"></div></div>`;
  } else if (p.imgur_album_id) {
    html = `<div class="${vc} imgur-album-wrap" data-iaid="${escHtml(p.imgur_album_id)}"><div class="rg-loading"></div></div>`;
  } else if (p.streamable_id) {
    html = `<div class="${vc}"><div class="streamable-embed"><iframe src="https://streamable.com/e/${escHtml(p.streamable_id)}" frameborder="0" width="100%" height="100%" allowfullscreen allow="autoplay"></iframe></div></div>`;
  } else if (p.embed_url) {
    html = `<div class="${vc}"><iframe src="${escHtml(p.embed_url)}" allowfullscreen loading="lazy" scrolling="no"></iframe></div>`;
  } else if (p.gif_url) {
    html = p.gif_is_video
      ? `<div class="${vc}"><video src="${escHtml(p.gif_url)}" controls autoplay loop muted playsinline></video></div>`
      : `<div class="${ic}"><img src="${escHtml(p.gif_url)}" loading="lazy" alt="" onerror="this.parentElement.classList.add('no-media')"></div>`;
  } else if (p.gallery?.length > (full ? 0 : 1)) {
    html = renderGallery(p.gallery);
  } else {
    const imgSrc = p.gallery?.length ? p.gallery[0].url : (!p.is_self ? p.preview_img : null);
    if (imgSrc) {
      html = `<div class="${ic}"><img src="${escHtml(imgSrc)}" loading="lazy" alt="" onerror="this.parentElement.classList.add('no-media')"></div>`;
    } else if (!p.is_self && p.url && /^https?:\/\//.test(p.url)) {
      html = `<div class="og-placeholder ${ic}" data-og-url="${escHtml(p.url)}"></div>`;
    }
  }
  if (!html) return '';
  if (p.is_spoiler) html = spoilerWrap(html);
  if (p.over_18)   html = nsfwWrap(html);
  return html;
}

export const mediaHtmlCard = p => mediaHtml(p, false);
export const mediaHtmlFull = p => mediaHtml(p, true);

// ── Gallery event delegation ─────────────────────────────────────────────────
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
  // Update pv-meta gallery download button if present
  const pvDlGallery = document.querySelector('.pv-dl-gallery');
  if (pvDlGallery && _dlOk(t.src)) {
    const fn = _dlFilenamePos(t.src, idx + 1);
    pvDlGallery.href = _dlHref(t.src, fn);
    pvDlGallery.download = fn;
  }
});

document.addEventListener('click', e => {
  const btn = e.target.closest('.devvit-try-btn');
  if (!btn) return;
  const card = btn.closest('.devvit-card');
  if (!card) return;
  const permalink = card.dataset.permalink;
  if (!permalink) return;
  btn.disabled = true;
  btn.textContent = 'Loading…';
  fetch(`/api/devvit?url=${encodeURIComponent(permalink)}`)
    .then(r => r.json())
    .then(d => {
      if (!d.embedded || !d.url) {
        btn.textContent = 'Not available';
        return;
      }
      const height = Math.max(300, Math.min(d.height || 512, 800));
      const iframe = document.createElement('iframe');
      iframe.src = d.url;
      iframe.className = 'devvit-iframe';
      iframe.style.height = `${height}px`;
      iframe.allow = 'autoplay; clipboard-write';
      iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups');
      card.replaceWith(iframe);
    })
    .catch(() => { btn.disabled = false; btn.textContent = 'Failed — retry'; });
});

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
