// Video playback: HLS players (loaded lazily) with a quality menu, separate Reddit
// audio tracks kept in sync, the shared mute/volume preference, and pausing or
// unloading animated media that scrolls out of view.
import { ownMedia, observeMedia, unobserveMedia, mediaDisposed, mediaSleeping } from './media-lifecycle.js';
import { state, setMutePref, setVolumePref } from './state.js';
import { BASE } from './utils.js';

export function trackVideoMute(v) {
  if (v.dataset.muteTracked) return;
  v.dataset.muteTracked = '1';
  ownMedia(v, {
    suspend: () => v.pause(),
    dispose: () => { v.pause(); v.removeAttribute('src'); v.load(); },
  });
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

export function syncAudio(videoEl, audioSrc) {
  const audio = new Audio(audioSrc);
  audio.preload = 'none';
  audio.volume = videoEl.volume;
  audio.muted = videoEl.muted;
  ownMedia(videoEl, {
    suspend: () => audio.pause(),
    dispose: () => { audio.pause(); audio.removeAttribute('src'); audio.load(); },
  });
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
    // Cache-busted via mtime so a deploy that changes hls.min.js (e.g. swapping the
    // vendored build) doesn't keep serving stale bytes to browsers that already
    // cached this URL under the 1-week static max-age.
    const v = window.__HLS_JS_V__;
    s.src = v ? `${BASE}/static/hls.min.js?v=${encodeURIComponent(v)}` : `${BASE}/static/hls.min.js`;
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
  if (mediaDisposed(videoEl)) return;
  if (hlsUrl && typeof Hls !== 'undefined' && Hls.isSupported()) {
    const hls = new Hls({ autoStartLoad: false, startLevel: 999 });
    let active = true;
    const start = () => { if (active && !mediaSleeping(videoEl)) hls.startLoad(); };
    const stop = () => { if (active) hls.stopLoad(); };
    const destroy = () => {
      if (!active) return;
      active = false;
      videoEl.removeEventListener('play', start);
      videoEl.removeEventListener('pause', stop);
      hls.destroy();
    };
    videoEl.addEventListener('play', start);
    videoEl.addEventListener('pause', stop);
    ownMedia(videoEl, {
      suspend: stop,
      dispose: destroy,
    });
    hls.loadSource(hlsUrl); hls.attachMedia(videoEl);
    hls.on(Hls.Events.ERROR, (_ev, data) => {
      if (!data.fatal) return;
      destroy();
      if (fallback) {
        const wrap = videoEl.closest('[data-hls]');
        if (wrap) wrap.querySelectorAll('.hls-quality-btn, .hls-quality-menu').forEach(el => el.remove());
        const wasPlaying = !videoEl.paused;
        videoEl.src = fallback;
        if (wasPlaying) videoEl.play().catch(() => {});
        if (audioSrc) syncAudio(videoEl, audioSrc);
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
    if (mediaDisposed(v) || mediaSleeping(v)) return;
    if (entry.isIntersecting) {
      if (!_reduceMotion()) v.play().catch(() => {});
    } else {
      v.pause();
    }
  });
}, { threshold: 0.1 });

// Reduced motion: gif videos stay paused (they keep their controls) instead of autoplaying.
function _reduceMotion() {
  return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

// Animated <img> gifs (giphy embeds in comments) have no play/pause API and decode
// every frame forever once loaded, even off-screen — a thread with many gif reactions
// tanks scroll performance. Drop the src when scrolled away and restore it on return.
// These <img> tags have no reserved size, so dropping the src would collapse them —
// the rendered size is pinned first, and the unload is delayed (cancelled if the gif
// re-enters view first) so a quick scroll pass doesn't reflow the thread.
const _imgGifUnloadTimers = new WeakMap();
const _IMG_GIF_UNLOAD_DELAY = 1500;
const _imgGifObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    const img = entry.target;
    if (mediaDisposed(img) || mediaSleeping(img)) return;
    if (entry.isIntersecting) {
      const t = _imgGifUnloadTimers.get(img);
      if (t) { clearTimeout(t); _imgGifUnloadTimers.delete(img); }
      if (!img.src && img.dataset.gifSrc) {
        // Keep the pinned size until the gif has decoded, so it doesn't collapse mid-reload.
        img.addEventListener('load', () => { img.style.width = img.style.height = ''; }, { once: true });
        img.src = img.dataset.gifSrc;
      }
    } else if (img.src && !_imgGifUnloadTimers.has(img)) {
      _imgGifUnloadTimers.set(img, setTimeout(() => {
        _imgGifUnloadTimers.delete(img);
        if (img.src) {
          // Pin the rendered size so removing the src doesn't collapse the img and shift the thread.
          const r = img.getBoundingClientRect();
          if (r.width && r.height) { img.style.width = `${r.width}px`; img.style.height = `${r.height}px`; }
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
    const clearTimer = () => {
      clearTimeout(_imgGifUnloadTimers.get(img));
      _imgGifUnloadTimers.delete(img);
    };
    ownMedia(img, { suspend: clearTimer, dispose: clearTimer });
    observeMedia(_imgGifObserver, img);
  });
}

export function initGifVideos(container) {
  container.querySelectorAll('video[autoplay]:not([data-gif-obs])').forEach(v => {
    v.dataset.gifObs = '1';
    v.removeAttribute('autoplay');
    observeMedia(_gifObserver, v);
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

// Feed and comment videos only attach HLS near the viewport. Attaching even
// with autoStartLoad disabled fetches a manifest, so defer the player itself.
const _hlsBuildObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting && !mediaDisposed(entry.target) && !mediaSleeping(entry.target)) {
      unobserveMedia(_hlsBuildObserver, entry.target);
      _buildHlsWrap(entry.target);
    }
  });
}, { rootMargin: '300px' });

export function initVideos(container) {
  container.querySelectorAll('[data-hls]:not([data-hls-init]):not([data-hls-obs])').forEach(wrap => {
    wrap.dataset.hlsObs = '1';
    observeMedia(_hlsBuildObserver, wrap);
  });
  container.querySelectorAll('video').forEach(trackVideoMute);
}
