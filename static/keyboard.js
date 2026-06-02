import { state } from './state.js';

export function initKeyboard({ navigate, feed, pvContent, postView, subInput, settingsPanel, closeSettingsPanel, closeLightbox }) {
  function _isTyping() {
    const tag = document.activeElement?.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || document.activeElement?.isContentEditable;
  }
  function _getPostEls() {
    return [...feed.querySelectorAll('.post, .post-compact')];
  }
  function _selectPost(idx) {
    const posts = _getPostEls();
    if (!posts.length) return;
    idx = Math.max(0, Math.min(idx, posts.length - 1));
    posts.forEach((p, i) => p.classList.toggle('keyboard-selected', i === idx));
    state.selectedPostIdx = idx;
    posts[idx].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  let _selectedCommentIdx = -1;
  document.addEventListener('pv-load', () => { _selectedCommentIdx = -1; });
  function _getTopCommentEls() {
    return [...pvContent.querySelectorAll('.comment[data-depth="0"]')];
  }
  function _selectComment(idx) {
    const comments = _getTopCommentEls();
    if (!comments.length) return;
    idx = Math.max(0, Math.min(idx, comments.length - 1));
    comments.forEach((c, i) => c.classList.toggle('keyboard-selected', i === idx));
    _selectedCommentIdx = idx;
    comments[idx].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      const kbdHelp = document.getElementById('kbd-help-overlay');
      if (kbdHelp) { kbdHelp.remove(); return; }
      if (settingsPanel.classList.contains('open')) { closeSettingsPanel(); return; }
      if (document.getElementById('lightbox').classList.contains('open')) { closeLightbox(); return; }
      if (postView.classList.contains('open') && state._pvSub) { history.back(); return; }
      return;
    }
    if (_isTyping()) return;
    if (postView.classList.contains('open')) {
      if (e.key === 'j' || e.key === 'J') { e.preventDefault(); _selectComment(_selectedCommentIdx + 1); }
      else if (e.key === 'k' || e.key === 'K') { e.preventDefault(); _selectComment(Math.max(0, _selectedCommentIdx - 1)); }
      return;
    }
    if (e.key === 'j' || e.key === 'J') {
      e.preventDefault();
      _selectPost(state.selectedPostIdx + 1);
    } else if (e.key === 'k' || e.key === 'K') {
      e.preventDefault();
      _selectPost(Math.max(0, state.selectedPostIdx - 1));
    } else if ((e.key === 'o' || e.key === 'Enter') && state.selectedPostIdx >= 0) {
      const posts = _getPostEls();
      const post = posts[state.selectedPostIdx];
      if (post) {
        const link = post.querySelector('a.post-title[data-nav], a.is-italic[data-nav]');
        if (link) navigate(link.dataset.nav);
      }
    } else if (e.key === '/') {
      e.preventDefault();
      subInput.focus();
      subInput.select();
    } else if (e.key === '?') {
      e.preventDefault();
      const existing = document.getElementById('kbd-help-overlay');
      if (existing) { existing.remove(); return; }
      const overlay = document.createElement('div');
      overlay.id = 'kbd-help-overlay';
      overlay.innerHTML = `<div class="kbd-help-box">
        <div class="kbd-help-title">Keyboard shortcuts</div>
        <div class="kbd-help-grid">
          <kbd>j</kbd><span>Next post / comment</span>
          <kbd>k</kbd><span>Previous post / comment</span>
          <kbd>o</kbd><span>Open selected post</span>
          <kbd>/</kbd><span>Focus search</span>
          <kbd>Esc</kbd><span>Close panel / lightbox</span>
          <kbd>?</kbd><span>Toggle this help</span>
        </div>
      </div>`;
      overlay.addEventListener('click', () => overlay.remove());
      document.body.appendChild(overlay);
    }
  });
}
