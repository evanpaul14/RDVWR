const lightbox    = document.getElementById('lightbox');
const lightboxImg = document.getElementById('lightbox-img');

let _lbScale = 1, _lbX = 0, _lbY = 0;
let _lbDragging = false, _lbDragX = 0, _lbDragY = 0;
let _lbPinchDist = 0, _lbPinchScale = 1;
let _lbTouchX = 0, _lbTouchY = 0, _lbTouchInitX = 0, _lbTouchInitY = 0;

function _lbApply() {
  lightboxImg.style.transform = _lbScale === 1 ? '' : `translate(${_lbX}px,${_lbY}px) scale(${_lbScale})`;
  lightboxImg.style.cursor = _lbScale > 1 ? (_lbDragging ? 'grabbing' : 'grab') : 'zoom-in';
}
function _lbReset() {
  _lbScale = 1; _lbX = 0; _lbY = 0; _lbDragging = false;
  lightboxImg.style.transform = ''; lightboxImg.style.cursor = '';
}
export function openLightbox(src) {
  lightboxImg.src = src; _lbReset();
  lightbox.classList.add('open');
  document.body.style.overflow = 'hidden';
}
export function closeLightbox() {
  lightbox.classList.remove('open');
  document.body.style.overflow = '';
  lightboxImg.src = ''; _lbReset();
}

lightbox.addEventListener('click', () => { if (!_lbDragging) closeLightbox(); });
lightboxImg.addEventListener('click', e => e.stopPropagation());
document.getElementById('lightbox-close').addEventListener('click', e => { e.stopPropagation(); closeLightbox(); });

lightboxImg.addEventListener('wheel', e => {
  if (!lightbox.classList.contains('open')) return;
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
  const newScale = Math.max(1, Math.min(8, _lbScale * factor));
  const rect = lightboxImg.getBoundingClientRect();
  const ox = e.clientX - (rect.left + rect.width / 2);
  const oy = e.clientY - (rect.top + rect.height / 2);
  _lbX = (_lbX - ox) * (newScale / _lbScale) + ox;
  _lbY = (_lbY - oy) * (newScale / _lbScale) + oy;
  _lbScale = newScale;
  if (_lbScale === 1) { _lbX = 0; _lbY = 0; }
  _lbApply();
}, { passive: false });

lightboxImg.addEventListener('dblclick', e => {
  e.stopPropagation();
  if (_lbScale > 1) { _lbScale = 1; _lbX = 0; _lbY = 0; }
  else {
    _lbScale = 2.5;
    const rect = lightboxImg.getBoundingClientRect();
    const ox = e.clientX - (rect.left + rect.width / 2);
    const oy = e.clientY - (rect.top + rect.height / 2);
    _lbX = -ox * (_lbScale - 1);
    _lbY = -oy * (_lbScale - 1);
  }
  _lbApply();
});

lightboxImg.addEventListener('mousedown', e => {
  if (_lbScale <= 1) return;
  e.preventDefault();
  _lbDragging = true; _lbDragX = e.clientX - _lbX; _lbDragY = e.clientY - _lbY;
  _lbApply();
});
document.addEventListener('mousemove', e => {
  if (!_lbDragging) return;
  _lbX = e.clientX - _lbDragX; _lbY = e.clientY - _lbDragY;
  _lbApply();
});
document.addEventListener('mouseup', () => { if (_lbDragging) { _lbDragging = false; _lbApply(); } });

lightboxImg.addEventListener('touchstart', e => {
  if (e.touches.length === 2) {
    _lbPinchDist = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
    _lbPinchScale = _lbScale;
  } else if (e.touches.length === 1 && _lbScale > 1) {
    _lbTouchX = e.touches[0].clientX; _lbTouchY = e.touches[0].clientY;
    _lbTouchInitX = _lbX; _lbTouchInitY = _lbY;
  }
}, { passive: true });
lightboxImg.addEventListener('touchmove', e => {
  if (e.touches.length === 2) {
    e.preventDefault();
    const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
    _lbScale = Math.max(1, Math.min(8, _lbPinchScale * (d / _lbPinchDist)));
    if (_lbScale === 1) { _lbX = 0; _lbY = 0; }
    _lbApply();
  } else if (e.touches.length === 1 && _lbScale > 1) {
    e.preventDefault();
    _lbX = _lbTouchInitX + (e.touches[0].clientX - _lbTouchX);
    _lbY = _lbTouchInitY + (e.touches[0].clientY - _lbTouchY);
    _lbApply();
  }
}, { passive: false });
