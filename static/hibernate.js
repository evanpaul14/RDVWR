// Keeps long infinite-scroll feeds light: post cards far outside the viewport
// have their contents detached (height locked so the scroll position doesn't
// move) and re-attached as they approach again. The outer .post element stays
// in place, so event delegation, keyboard indices and visited classes still work.
const WAKE_MARGIN = '3000px 0px';
const _stash = new WeakMap();

const _observer = new IntersectionObserver(entries => {
  for (const { target, isIntersecting, boundingClientRect } of entries) {
    if (isIntersecting) _wake(target);
    // Zero height means the card is hidden (read-hidden, feed not laid out) — leave it.
    else if (boundingClientRect.height > 0) _sleep(target, boundingClientRect.height);
  }
}, { rootMargin: WAKE_MARGIN });

function _sleep(card, height) {
  if (_stash.has(card) || card.contains(document.activeElement)) return;
  const frag = document.createDocumentFragment();
  while (card.firstChild) frag.appendChild(card.firstChild);
  card.style.height = `${height}px`;
  card.classList.add('post-hibernated');
  _stash.set(card, frag);
}

function _wake(card) {
  const frag = _stash.get(card);
  if (!frag) return;
  _stash.delete(card);
  card.appendChild(frag);
  card.style.height = '';
  card.classList.remove('post-hibernated');
}

/** The element to query inside a card, whether it is currently hibernated or not. */
export function cardContent(card) {
  return _stash.get(card) ?? card;
}

/** Observe every .post card added directly to `feed`; stop observing removed ones. */
export function initCardHibernation(feed) {
  feed.querySelectorAll(':scope > .post').forEach(card => _observer.observe(card));
  new MutationObserver(mutations => {
    for (const m of mutations) {
      for (const n of m.removedNodes) if (n.nodeType === 1) _observer.unobserve(n);
      for (const n of m.addedNodes) if (n.nodeType === 1 && n.classList.contains('post')) _observer.observe(n);
    }
  }).observe(feed, { childList: true });
}
