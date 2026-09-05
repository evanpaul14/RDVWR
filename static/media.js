import { state, setMutePref, setVolumePref } from './state.js';
import { settings } from './settings.js';
import { escHtml, evictMap, renderPoll, GALLERY_SWIPE_MIN } from './utils.js';

function _trackVideoMute(v) {
  if (v.dataset.muteTracked) return;
  v.dataset.muteTracked = '1';
  v.volume = state.userVolume;
  v.muted = state.userPrefersMuted;
  v.addEventListener('volumechange', () => {
    if (v.volume !== state.userVolume) {
      setVolumePref(v.volume);
      document.querySelectorAll('video[data-mute-tracked]').forEach(other => {
        if (other !== v) other.volume = v.volume;
      });
    }
    const nowMuted = v.muted || v.volume === 0;
    if (nowMuted !== state.userPrefersMuted) {
      setMutePref(nowMuted);
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
  audio.volume = videoEl.volume;
  audio.muted = videoEl.muted;
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
    // hls.light.min.js (the bundle we vendor) has no alt-audio-track support, so it
    // never fetches Reddit's separate audio rendition even though the manifest lists
    // it — sync it manually via a parallel <audio> element instead. This stays active
    // across the fatal-error fallback below too, since it's driven by videoEl's own
    // play/pause/seeked events rather than the HLS instance.
    if (audioSrc) syncAudio(videoEl, audioSrc);
    hls.on(Hls.Events.ERROR, (_ev, data) => {
      if (!data.fatal) return;
      hls.destroy();
      if (fallback) {
        const wrap = videoEl.closest('[data-hls]');
        if (wrap) wrap.querySelectorAll('.hls-quality-btn, .hls-quality-menu').forEach(el => el.remove());
        const wasPlaying = !videoEl.paused;
        videoEl.src = fallback;
        if (wasPlaying) videoEl.play().catch(() => {});
      }
    });
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
      const onDocClick = () => {
        if (!document.body.contains(menu)) { document.removeEventListener('click', onDocClick); return; }
        menu.classList.remove('open');
        hideBtn();
      };
      document.addEventListener('click', onDocClick, { passive: true });
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
    if (audioSrc) syncAudio(videoEl, audioSrc);
  }
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

// Animated <img> gifs (giphy embeds in comments) have no play/pause API and decode
// every frame forever once loaded, even off-screen — a thread with many gif reactions
// tanks scroll performance. Drop the src when scrolled away and restore it on return.
// These <img> tags have no reserved size, so dropping the src collapses them — done
// on a short delay (cancelled if the gif re-enters view first) so a quick scroll pass
// doesn't collapse-and-reflow the thread out from under the reader.
const _imgGifUnloadTimers = new WeakMap();
const _IMG_GIF_UNLOAD_DELAY = 1500;
const _imgGifObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    const img = entry.target;
    if (entry.isIntersecting) {
      const t = _imgGifUnloadTimers.get(img);
      if (t) { clearTimeout(t); _imgGifUnloadTimers.delete(img); }
      if (!img.src && img.dataset.gifSrc) img.src = img.dataset.gifSrc;
    } else if (img.src && !_imgGifUnloadTimers.has(img)) {
      _imgGifUnloadTimers.set(img, setTimeout(() => {
        _imgGifUnloadTimers.delete(img);
        if (img.src) {
          img.dataset.gifSrc = img.src;
          img.removeAttribute('src');
        }
      }, _IMG_GIF_UNLOAD_DELAY));
    }
  });
}, { rootMargin: '200px' });

export function initGifImages(container) {
  container.querySelectorAll('img.gif-anim-img:not([data-gif-img-obs])').forEach(img => {
    img.dataset.gifImgObs = '1';
    _imgGifObserver.observe(img);
  });
}

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

function _buildHlsWrap(wrap) {
  if (wrap.dataset.hlsInit) return;
  wrap.dataset.hlsInit = '1';
  const v = wrap.querySelector('video');
  if (v) {
    setupHls(v, wrap.dataset.hls, wrap.dataset.src, wrap.dataset.audio);
    if (wrap.dataset.poster) v.poster = wrap.dataset.poster;
  }
}

// Reddit-video gif reactions embedded in comment markdown (.md-video-embed) are
// built lazily like redgifs below — a gif-heavy thread can have many of these, and
// eagerly attaching hls.js to every one fires a manifest fetch per embed on mount.
const _hlsBuildObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      _hlsBuildObserver.unobserve(entry.target);
      _buildHlsWrap(entry.target);
    }
  });
}, { rootMargin: '300px' });

export function initVideos(container) {
  container.querySelectorAll('[data-hls]:not([data-hls-init]):not([data-hls-obs])').forEach(wrap => {
    if (wrap.classList.contains('md-video-embed')) {
      wrap.dataset.hlsObs = '1';
      _hlsBuildObserver.observe(wrap);
    } else {
      _buildHlsWrap(wrap);
    }
  });
  container.querySelectorAll('video').forEach(_trackVideoMute);
}

export function observeRedgifsPrefetch(container) {
  container.querySelectorAll('.redgifs-wrap[data-rgid]:not([data-rg-prefetch])').forEach(w => {
    w.dataset.rgPrefetch = '1';
    _rgPrefetchObserver.observe(w);
    _rgBuildObserver.observe(w);
  });
}

async function _buildRedgifsWrap(wrap) {
  const id = wrap.dataset.rgid;
  let data = _rgCache.get(id);
  if (!data) { _prefetchRedgifs(id); data = _rgCache.get(id); }
  data = (await data) ?? null;
  if (!data || (!data.hd && !data.sd)) {
    const fbHls = wrap.dataset.fbHls, fbSrc = wrap.dataset.fbSrc;
    if (fbHls || fbSrc) {
      wrap.dataset.hls = fbHls || '';
      wrap.dataset.src = fbSrc || '';
      wrap.innerHTML = `<div class="rg-fallback-badge" title="Original video was removed from RedGifs — playing Reddit's mirrored copy, which has no audio">fallback · no audio</div><video controls preload="metadata" playsinline muted></video>`;
      setupHls(wrap.querySelector('video'), fbHls, fbSrc, null);
      _trackVideoMute(wrap.querySelector('video'));
    } else {
      wrap.innerHTML = `<div class="rg-error">Could not load video</div>`;
    }
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
    if (!entry.isIntersecting) return;
    const wrap = entry.target;
    if (wrap.dataset.rgInit) return;
    wrap.dataset.rgInit = '1';
    _rgBuildObserver.unobserve(wrap);
    _rgPrefetchObserver.unobserve(wrap);
    _rgBuildQueue.push(wrap);
    if (!_rgBuildScheduled) {
      _rgBuildScheduled = true;
      setTimeout(_flushRgBuildQueue, 0);
    }
  });
}, { rootMargin: '200px' });

export function initMedia(container) {
  initVideos(container);
  observeRedgifsPrefetch(container);
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
  if (settings.layout === 'minimal') return;
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
    `<img class="gallery-thumb${i===0?' active':''}" src="${escHtml(img.url)}" data-idx="${i}" data-caption="${escHtml(img.caption||'')}" data-w="${img.width||''}" data-h="${img.height||''}" loading="lazy" alt="${escHtml(img.caption||'')}">`
  ).join('');
  return `
    <div class="gallery">
      <div class="gallery-stage">
        <img class="gallery-main-img" src="${escHtml(images[0].url)}" alt="${escHtml(images[0].caption||'')}"${images[0].width ? ` width="${images[0].width}" height="${images[0].height}"` : ''}>
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

// ── Minimal mode: plain thumbnail + link, no video/iframe/gallery-nav embeds ──
function _minimalMediaHtml(p, full) {
  const ic = full ? 'pv-media' : 'post-media';
  const thumb = p.gallery?.[0]?.url ?? (full ? p.preview_img : (p.thumb_url ?? p.preview_img)) ?? null;
  const thumbHtml = thumb ? `<img src="${escHtml(thumb)}" loading="lazy" alt="">` : '';
  let label = '', href = p.url || '';
  if (p.is_video)                  { label = 'video ↗'; href = p.video_url || p.url; }
  else if (p.youtube_id)           { label = 'YouTube video ↗'; href = `https://www.youtube.com/watch?v=${p.youtube_id}`; }
  else if (p.tiktok_id)            { label = 'TikTok video ↗'; }
  else if (p.redgifs_id)           { label = 'video ↗'; }
  else if (p.imgur_album_id)       { label = 'Imgur album ↗'; }
  else if (p.streamable_id)        { label = 'video ↗'; }
  else if (p.embed_url)            { label = 'embedded media ↗'; }
  else if (p.gif_url && p.gif_is_video) { label = 'gif ↗'; href = p.gif_url; }
  else if (p.gif_url)              { return `<div class="${ic}"><img src="${escHtml(p.gif_url)}" loading="lazy" alt=""></div>`; }
  else if (p.gallery?.length > 1)  {
    const imgs = p.gallery.map(img => `<img src="${escHtml(img.url)}" loading="lazy" alt="${escHtml(img.caption||'')}">`).join('');
    return `<div class="${ic} minimal-gallery-stack">${imgs}</div>`;
  }
  else if (thumb)                  { return `<div class="${ic}">${thumbHtml}</div>`; }
  else if (!p.is_self && p.url && /^https?:\/\//.test(p.url)) { label = `${(() => { try { return new URL(p.url).hostname; } catch { return 'link'; } })()} ↗`; }
  else return '';
  if (!href) return '';
  return `<div class="${ic} minimal-media">${thumbHtml}<a class="minimal-media-link" href="${escHtml(href)}" target="_blank" rel="noopener">${escHtml(label)}</a></div>`;
}

export function mediaHtml(p, full = false) {
  if (p.poll) return renderPoll(p.poll);
  if (settings.layout === 'minimal') {
    let html = _minimalMediaHtml(p, full);
    if (!html) return '';
    if (p.is_spoiler) html = spoilerWrap(html);
    if (p.over_18)   html = nsfwWrap(html);
    return html;
  }
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
          <button class="devvit-try-btn" title="Try the interactive app">Try App</button>
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
    html = `<div class="${vc} redgifs-wrap" data-rgid="${escHtml(p.redgifs_id)}" data-fb-hls="${escHtml(p.redgifs_fallback_hls||'')}" data-fb-src="${escHtml(p.redgifs_fallback_url||'')}"><div class="rg-loading"></div></div>`;
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
    const imgSrc = p.gallery?.length ? p.gallery[0].url : (!p.is_self ? (full ? p.preview_img : (p.thumb_url ?? p.preview_img)) : null);
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
  if (t.dataset.w) { mainImg.width = t.dataset.w; mainImg.height = t.dataset.h; }
  else { mainImg.removeAttribute('width'); mainImg.removeAttribute('height'); }
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
  fetch(`/api/devvit?url=${encodeURIComponent(permalink)}`, { cache: 'no-store' })
    .then(r => r.json())
    .then(d => {
      if (!d.embedded || !d.url) {
        btn.textContent = 'Not available';
        return;
      }
      // Without a parent Reddit page doing the postMessage handshake, the
      // Devvit SDK falls back to reading its render context (post id, poll
      // state, signed auth token) from the URL hash — forward what the
      // server already extracted so the app renders instead of erroring.
      const url = d.bridge && Object.keys(d.bridge).length
        ? `${d.url}#${encodeURIComponent(JSON.stringify(d.bridge))}`
        : d.url;
      // Devvit webviews send frame-ancestors allowing only reddit.com and
      // localhost/127.0.0.1 (dev/loopback testing) — anywhere else, inline
      // iframing is blocked by the browser, so fall back to a new tab.
      const isLoopback = ['localhost', '127.0.0.1'].includes(location.hostname);
      if (!isLoopback) {
        window.open(url, '_blank', 'noopener');
        btn.disabled = false;
        btn.textContent = 'Try App';
        return;
      }
      const height = Math.max(300, Math.min(d.height || 512, 800));
      const iframe = document.createElement('iframe');
      iframe.src = url;
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
