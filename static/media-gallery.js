// Galleries: markup, prev/next/thumbnail navigation and swipe.
import { escHtml, GALLERY_SWIPE_MIN } from './utils.js';
import { dlOk, dlHref, dlFilenamePos } from './media-download.js';

// Post-view images open the lightbox on click; these make them reachable and
// operable from the keyboard too (interactions.js turns Enter/Space into a click).
export const ZOOM_ATTRS = ' tabindex="0" role="button" aria-label="View image full size"';

// Gallery items carry downscaled `thumb` (≥640px, for feed cards) and `mini` (strip
// thumbnails) renditions; Imgur albums and older payloads only have `url`.
export const galleryThumb = g => g.thumb || g.url;
export const galleryMini  = g => g.mini || g.thumb || g.url;

// `full` (post view) shows originals on the stage; feed cards show the card rendition.
// data-full always holds the original, for the lightbox and downloads.
export function renderGallery(images, full = true) {
  if (!images?.length) return '';
  const stageSrc = img => full ? img.url : galleryThumb(img);
  const thumbsHtml = images.map((img,i) =>
    `<img class="gallery-thumb${i===0?' active':''}" tabindex="0" role="button" aria-label="Image ${i+1} of ${images.length}"${i===0?' aria-current="true"':''} src="${escHtml(galleryMini(img))}" data-main="${escHtml(stageSrc(img))}" data-full="${escHtml(img.url)}" data-idx="${i}" data-caption="${escHtml(img.caption||'')}" data-w="${img.width||''}" data-h="${img.height||''}" loading="lazy" alt="${escHtml(img.caption||'')}">`
  ).join('');
  return `
    <div class="gallery">
      <div class="gallery-stage">
        <img class="gallery-main-img"${full ? ZOOM_ATTRS : ''} src="${escHtml(stageSrc(images[0]))}" data-full="${escHtml(images[0].url)}" alt="${escHtml(images[0].caption||'')}"${images[0].width ? ` width="${images[0].width}" height="${images[0].height}"` : ''}>
        ${images.length > 1 ? `
          <div class="gallery-nav">
            <button class="gallery-btn gallery-prev" aria-label="Previous image" disabled>‹</button>
            <span class="gallery-counter" aria-live="polite">1 / ${images.length}</span>
            <button class="gallery-btn gallery-next" aria-label="Next image">›</button>
          </div>` : ''}
      </div>
      ${images[0].caption ? `<div class="gallery-caption">${escHtml(images[0].caption)}</div>` : ''}
      ${images.length > 1 ? `<div class="gallery-thumbs">${thumbsHtml}</div>` : ''}
    </div>`;
}

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
  mainImg.src = t.dataset.main || t.src; mainImg.alt = t.alt;
  mainImg.dataset.full = t.dataset.full || t.src;
  if (t.dataset.w) { mainImg.width = t.dataset.w; mainImg.height = t.dataset.h; }
  else { mainImg.removeAttribute('width'); mainImg.removeAttribute('height'); }
  if (counter) counter.textContent = `${idx+1} / ${thumbs.length}`;
  if (prevBtn) prevBtn.disabled = idx === 0;
  if (nextBtn) nextBtn.disabled = idx === thumbs.length - 1;
  if (caption) { caption.textContent = t.dataset.caption; caption.style.display = t.dataset.caption ? '' : 'none'; }
  thumbs.forEach((t,i) => {
    t.classList.toggle('active', i === idx);
    if (i === idx) t.setAttribute('aria-current', 'true'); else t.removeAttribute('aria-current');
  });
  // Update pv-meta gallery download button if present
  const pvDlGallery = document.querySelector('.pv-dl-gallery');
  const fullSrc = mainImg.dataset.full;
  if (pvDlGallery && dlOk(fullSrc)) {
    const fn = dlFilenamePos(fullSrc, idx + 1);
    pvDlGallery.href = dlHref(fullSrc, fn);
    pvDlGallery.download = fn;
  }
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
