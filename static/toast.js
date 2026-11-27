// Short status messages at the bottom of the screen. One live region is reused, so
// screen readers announce each message and a new one replaces the last.
let _el = null;
let _timer = 0;

export function showToast(message, { timeout = 4000 } = {}) {
  if (!_el) {
    _el = document.createElement('div');
    _el.className = 'toast';
    _el.setAttribute('role', 'status');
    _el.setAttribute('aria-live', 'polite');
    document.body.appendChild(_el);
  }
  clearTimeout(_timer);
  _el.textContent = message;
  _el.classList.add('show');
  _timer = setTimeout(() => _el.classList.remove('show'), timeout);
}
