import { escHtml, AUTOCOMPLETE_DEBOUNCE } from './utils.js';

const _acCancellers = [];

export function hideAllAutocomplete() {
  _acCancellers.forEach(fn => fn());
}

export function setupAutocomplete(inputEl, dropdownEl, navigate) {
  let acTimer = null;
  let acIdx = -1;
  let preAcVal = '';
  let acAbort = null;

  function hide() {
    dropdownEl.classList.remove('open');
    dropdownEl.innerHTML = '';
    acIdx = -1;
    inputEl.setAttribute('aria-expanded', 'false');
  }
  function cancel() {
    clearTimeout(acTimer);
    if (acAbort) { acAbort.abort(); acAbort = null; }
    hide();
  }
  function show(names) {
    if (!names.length) { hide(); return; }
    acIdx = -1;
    dropdownEl.innerHTML = names.map(n =>
      `<div class="autocomplete-item" role="option" data-sub="${escHtml(n)}">${escHtml(n)}</div>`
    ).join('');
    dropdownEl.classList.add('open');
    inputEl.setAttribute('aria-expanded', 'true');
  }

  inputEl.addEventListener('input', () => {
    clearTimeout(acTimer);
    if (acAbort) { acAbort.abort(); acAbort = null; }
    const val = inputEl.value.trim();
    const query = val.startsWith('r/') ? val.slice(2) : val;
    if (query.length < 2) { hide(); return; }
    acTimer = setTimeout(async () => {
      const ctrl = new AbortController();
      acAbort = ctrl;
      try {
        const res  = await fetch(`/api/subreddit-search?q=${encodeURIComponent(query)}`, { signal: ctrl.signal });
        const data = await res.json();
        acAbort = null;
        show(data.names || []);
      } catch (err) {
        if (err.name !== 'AbortError') hide();
      }
    }, AUTOCOMPLETE_DEBOUNCE);
  });

  inputEl.addEventListener('keydown', e => {
    if (!dropdownEl.classList.contains('open')) return;
    const items = [...dropdownEl.querySelectorAll('.autocomplete-item')];
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (acIdx === -1) preAcVal = inputEl.value;
      acIdx = Math.min(acIdx + 1, items.length - 1);
      items.forEach((item, i) => item.classList.toggle('focused', i === acIdx));
      if (acIdx >= 0) inputEl.value = items[acIdx].dataset.sub;
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      acIdx = Math.max(acIdx - 1, -1);
      items.forEach((item, i) => item.classList.toggle('focused', i === acIdx));
      inputEl.value = acIdx >= 0 ? items[acIdx].dataset.sub : preAcVal;
    } else if (e.key === 'Enter') {
      const selectedIdx = acIdx;
      cancel();
      if (selectedIdx >= 0) {
        e.preventDefault();
        e.stopImmediatePropagation();
        const sub = items[selectedIdx]?.dataset.sub;
        if (sub) navigate(`/r/${sub}`);
      }
    } else if (e.key === 'Escape') {
      e.stopImmediatePropagation();
      cancel();
    }
  }, true);

  inputEl.addEventListener('blur', () => { setTimeout(cancel, 150); });
  dropdownEl.addEventListener('mousedown', e => e.preventDefault());
  dropdownEl.addEventListener('click', e => {
    const item = e.target.closest('.autocomplete-item');
    if (!item) return;
    cancel();
    navigate(`/r/${item.dataset.sub}`);
  });
  return cancel;
}

export function initAutocomplete(subInput, pvSubInput, navigate) {
  _acCancellers.push(setupAutocomplete(subInput, document.getElementById('autocomplete-dropdown'), navigate));
  _acCancellers.push(setupAutocomplete(pvSubInput, document.getElementById('pv-autocomplete-dropdown'), navigate));
}
