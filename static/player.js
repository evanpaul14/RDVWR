import { state } from './state.js';

const I_PLAY  = `<svg viewBox="0 0 16 16" fill="currentColor"><polygon points="3,1 14,8 3,15"/></svg>`;
const I_PAUSE = `<svg viewBox="0 0 16 16" fill="currentColor"><rect x="2" y="1" width="5" height="14" rx="1"/><rect x="9" y="1" width="5" height="14" rx="1"/></svg>`;
const I_VOL   = `<svg viewBox="0 0 16 16"><path fill="currentColor" d="M3 5H1a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h2l3 3V2L3 5z"/><path stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" d="M9 4.5a4.5 4.5 0 0 1 0 7"/></svg>`;
const I_MUTE  = `<svg viewBox="0 0 16 16"><path fill="currentColor" d="M3 5H1a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h2l3 3V2L3 5z"/><line x1="11" y1="6" x2="15" y2="10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="15" y1="6" x2="11" y2="10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
const I_FULL  = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"/></svg>`;
const I_EXIT  = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M6 2v4H2M10 2v4h4M14 10h-4v4M6 14v-4H2"/></svg>`;

function fmt(s) {
  if (!isFinite(s) || s < 0) return '0:00';
  const m = Math.floor(s / 60), ss = Math.floor(s % 60);
  return `${m}:${ss.toString().padStart(2, '0')}`;
}

export function initCustomPlayer(videoEl) {
  if (videoEl.dataset.vpInit || videoEl.loop) return;
  videoEl.dataset.vpInit = '1';
  videoEl.removeAttribute('controls');

  const wrap = videoEl.parentElement;
  if (!wrap) return;
  wrap.classList.add('vp-wrap');

  const overlay = document.createElement('div');
  overlay.className = 'vp-overlay';

  const ctrl = document.createElement('div');
  ctrl.className = 'vp-controls';
  ctrl.innerHTML = `
    <div class="vp-prog-row">
      <div class="vp-progress">
        <div class="vp-buf"></div>
        <div class="vp-fill"></div>
        <div class="vp-knob"></div>
      </div>
    </div>
    <div class="vp-bar">
      <button class="vp-btn vp-play" title="Play / Pause">${I_PLAY}</button>
      <span class="vp-time">0:00 / 0:00</span>
      <div class="vp-spacer"></div>
      <div class="vp-quality-slot"></div>
      <button class="vp-btn vp-mute" title="Toggle mute">${I_VOL}</button>
      <input class="vp-vol-slider" type="range" min="0" max="1" step="0.02" value="1" title="Volume" aria-label="Volume">
      <button class="vp-btn vp-full" title="Fullscreen">${I_FULL}</button>
    </div>`;

  wrap.append(overlay, ctrl);

  const playBtn  = ctrl.querySelector('.vp-play');
  const muteBtn  = ctrl.querySelector('.vp-mute');
  const fullBtn  = ctrl.querySelector('.vp-full');
  const volSlider = ctrl.querySelector('.vp-vol-slider');
  const timeEl   = ctrl.querySelector('.vp-time');
  const progEl   = ctrl.querySelector('.vp-progress');
  const fillEl   = ctrl.querySelector('.vp-fill');
  const bufEl    = ctrl.querySelector('.vp-buf');
  const knobEl   = ctrl.querySelector('.vp-knob');

  // Play / pause
  function setPlayIcon() {
    playBtn.innerHTML = videoEl.paused ? I_PLAY : I_PAUSE;
    wrap.classList.toggle('vp-playing', !videoEl.paused);
  }
  function togglePlay() {
    if (videoEl.paused) videoEl.play().catch(() => {});
    else videoEl.pause();
  }
  playBtn.addEventListener('click', e => { e.stopPropagation(); togglePlay(); });
  videoEl.addEventListener('play',  setPlayIcon);
  videoEl.addEventListener('pause', setPlayIcon);
  videoEl.addEventListener('ended', setPlayIcon);

  // Volume + mute
  let _lastVol = videoEl.volume || 1;

  function syncVol() {
    const muted = videoEl.muted || videoEl.volume === 0;
    muteBtn.innerHTML = muted ? I_MUTE : I_VOL;
    const displayVol = muted ? 0 : videoEl.volume;
    volSlider.value = displayVol;
    volSlider.style.setProperty('--vol', (displayVol * 100) + '%');
  }

  muteBtn.addEventListener('click', e => {
    e.stopPropagation();
    if (videoEl.muted || videoEl.volume === 0) {
      videoEl.muted = false;
      videoEl.volume = _lastVol || 1;
    } else {
      _lastVol = videoEl.volume;
      videoEl.muted = true;
    }
    state.userPrefersMuted = videoEl.muted;
    localStorage.setItem('mutePreference', videoEl.muted ? 'muted' : 'unmuted');
    syncVol();
  });

  volSlider.addEventListener('input', e => {
    e.stopPropagation();
    const v = parseFloat(volSlider.value);
    if (v === 0) {
      videoEl.muted = true;
    } else {
      videoEl.muted = false;
      videoEl.volume = v;
      _lastVol = v;
    }
    state.userPrefersMuted = videoEl.muted;
    localStorage.setItem('mutePreference', videoEl.muted ? 'muted' : 'unmuted');
    syncVol();
  });
  volSlider.addEventListener('click',      e => e.stopPropagation());
  volSlider.addEventListener('mousedown',  e => e.stopPropagation());
  volSlider.addEventListener('touchstart', e => e.stopPropagation(), { passive: true });

  videoEl.addEventListener('volumechange', syncVol);
  syncVol();

  // Progress
  function syncProgress() {
    const dur = videoEl.duration || 0;
    const cur = videoEl.currentTime;
    if (dur > 0) {
      const pct = (cur / dur) * 100;
      fillEl.style.width = pct + '%';
      knobEl.style.left  = pct + '%';
      let bufEnd = 0;
      for (let i = 0; i < videoEl.buffered.length; i++)
        if (videoEl.buffered.end(i) > bufEnd) bufEnd = videoEl.buffered.end(i);
      bufEl.style.width = ((bufEnd / dur) * 100) + '%';
    } else {
      fillEl.style.width = knobEl.style.left = bufEl.style.width = '0%';
    }
    timeEl.textContent = `${fmt(cur)} / ${fmt(dur)}`;
  }
  videoEl.addEventListener('timeupdate',     syncProgress);
  videoEl.addEventListener('durationchange', syncProgress);
  videoEl.addEventListener('progress',       syncProgress);

  // Seeking
  let seeking = false;
  function doSeek(clientX) {
    const r = progEl.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
    videoEl.currentTime = pct * (videoEl.duration || 0);
    syncProgress();
  }
  progEl.addEventListener('mousedown', e => {
    e.preventDefault(); seeking = true; doSeek(e.clientX); showCtrl();
  });
  document.addEventListener('mousemove', e => { if (seeking) doSeek(e.clientX); });
  document.addEventListener('mouseup',   e => { if (seeking) { seeking = false; doSeek(e.clientX); } });
  progEl.addEventListener('touchstart', e => {
    e.stopPropagation(); seeking = true; doSeek(e.touches[0].clientX); showCtrl();
  }, { passive: true });
  document.addEventListener('touchmove', e => {
    if (seeking) doSeek(e.touches[0].clientX);
  }, { passive: true });
  document.addEventListener('touchend', e => {
    if (seeking) { seeking = false; if (e.changedTouches[0]) doSeek(e.changedTouches[0].clientX); }
  }, { passive: true });

  // Fullscreen
  function onFsChange() {
    const inFs = document.fullscreenElement === wrap || document.webkitFullscreenElement === wrap;
    fullBtn.innerHTML = inFs ? I_EXIT : I_FULL;
    fullBtn.title = inFs ? 'Exit fullscreen' : 'Fullscreen';
  }
  fullBtn.addEventListener('click', e => {
    e.stopPropagation();
    if (!document.fullscreenElement && !document.webkitFullscreenElement) {
      if (wrap.requestFullscreen) wrap.requestFullscreen().catch(() => {});
      else if (wrap.webkitRequestFullscreen) wrap.webkitRequestFullscreen();
      else if (videoEl.webkitEnterFullscreen) videoEl.webkitEnterFullscreen();
    } else {
      if (document.exitFullscreen) document.exitFullscreen();
      else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
    }
  });
  document.addEventListener('fullscreenchange',       onFsChange);
  document.addEventListener('webkitfullscreenchange', onFsChange);

  // Auto-hide controls
  let hideTimer;
  function showCtrl() {
    wrap.classList.add('vp-ctrl-show');
    clearTimeout(hideTimer);
    if (!videoEl.paused) {
      hideTimer = setTimeout(() => {
        if (!seeking) wrap.classList.remove('vp-ctrl-show');
      }, 2500);
    }
  }
  wrap.addEventListener('mousemove', showCtrl, { passive: true });
  wrap.addEventListener('mouseleave', () => {
    if (!videoEl.paused) { clearTimeout(hideTimer); wrap.classList.remove('vp-ctrl-show'); }
  }, { passive: true });
  videoEl.addEventListener('play',  showCtrl);
  videoEl.addEventListener('pause', () => { clearTimeout(hideTimer); wrap.classList.add('vp-ctrl-show'); });

  // Touch: first tap shows controls; second tap plays/pauses
  let touchShowed = false;
  wrap.addEventListener('touchstart', () => {
    touchShowed = !wrap.classList.contains('vp-ctrl-show');
    showCtrl();
  }, { passive: true });
  overlay.addEventListener('click', () => {
    if (touchShowed) { touchShowed = false; return; }
    togglePlay();
  });

  // Initial state
  wrap.classList.add('vp-ctrl-show');
  if (!state.userPrefersMuted) { videoEl.muted = false; syncVol(); }
}
