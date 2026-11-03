import { state } from './state.js';
import { settings } from './settings.js';
import { parseRoute } from './router.js';
import { hideAllAutocomplete } from './autocomplete.js';
import { navigate, onRoute } from './nav.js';

const subInput          = document.getElementById('subreddit-input');
const pvSubInput        = document.getElementById('pv-subreddit-input');
const mobileSearchInput = document.getElementById('mobile-search-input');

// ── Bottom nav & mobile search ────────────────────────────────────────────────
function updateBottomNav(route) {
  const bnHome   = document.getElementById('bn-home');
  const bnSearch = document.getElementById('bn-search');
  const bnSaved  = document.getElementById('bn-saved');
  if (!bnHome) return;
  [bnHome, bnSearch, bnSaved].forEach(b => b?.classList.remove('active'));
  const headerSearchBtn = document.getElementById('header-search-btn');
  if (document.body.classList.contains('mobile-search-open') || route.type === 'search') {
    bnSearch?.classList.add('active');
    headerSearchBtn?.classList.add('active');
  } else if (route.type === 'saved') {
    bnSaved?.classList.add('active');
    headerSearchBtn?.classList.remove('active');
  } else {
    bnHome.classList.add('active');
    headerSearchBtn?.classList.remove('active');
  }
}

export function openMobileSearch() {
  document.body.classList.add('mobile-search-open');
  document.getElementById('bn-search')?.classList.add('active');
  document.getElementById('header-search-btn')?.classList.add('active');
  document.getElementById('pv-search-toggle')?.classList.add('active');
  mobileSearchInput?.focus();
}

function closeMobileSearch() {
  document.body.classList.remove('mobile-search-open');
  document.getElementById('bn-search')?.classList.remove('active');
  document.getElementById('header-search-btn')?.classList.remove('active');
  document.getElementById('pv-search-toggle')?.classList.remove('active');
  if (mobileSearchInput) mobileSearchInput.value = '';
  hideAllAutocomplete();
}

onRoute(route => { closeMobileSearch(); updateBottomNav(route); });

// Search input
function handleSearchInput(e) {
  let activeInput;
  if (e?.currentTarget?.id === 'pv-search-btn' || e?.target === pvSubInput || document.activeElement === pvSubInput) {
    activeInput = pvSubInput;
  } else if (e?.currentTarget?.id === 'mobile-search-btn' || e?.target === mobileSearchInput || document.activeElement === mobileSearchInput) {
    activeInput = mobileSearchInput;
  } else {
    activeInput = subInput;
  }
  const val = activeInput.value.trim();
  if (!val) return;
  const mMultiInput = val.match(/^u(?:ser)?\/([^\/]+)\/m\/([^\/]+)/i);
  if (mMultiInput) { navigate(`/user/${mMultiInput[1]}/m/${mMultiInput[2]}`); return; }
  if (val.startsWith('r/')) {
    const sub = val.slice(2).replace(/^\//, '');
    if (sub) navigate(`/r/${sub}/${settings.subSort}`);
  } else {
    const sub = state.searchMode ? state.searchSub : (state.currentSub || '');
    let url = `/search?q=${encodeURIComponent(val)}`;
    if (sub) url += `&sub=${encodeURIComponent(sub)}`;
    navigate(url);
  }
}
document.getElementById('search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
document.getElementById('subreddit-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') { hideAllAutocomplete(); handleSearchInput(); }
});
document.getElementById('pv-search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
document.getElementById('pv-subreddit-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') { hideAllAutocomplete(); handleSearchInput(e); }
});
document.getElementById('mobile-search-btn').addEventListener('click', e => { hideAllAutocomplete(); handleSearchInput(e); });
mobileSearchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') { hideAllAutocomplete(); handleSearchInput(e); }
  if (e.key === 'Escape') { closeMobileSearch(); }
});
mobileSearchInput.addEventListener('blur', () => {
  setTimeout(() => {
    if (!document.activeElement?.closest('#mobile-search-bar')) closeMobileSearch();
  }, 200);
});

// Bottom nav search tab
document.getElementById('bn-search').addEventListener('click', () => {
  if (document.body.classList.contains('mobile-search-open')) {
    closeMobileSearch();
    updateBottomNav(parseRoute());
  } else {
    openMobileSearch();
  }
});

// Post-view search toggle (mobile)
document.getElementById('pv-search-toggle')?.addEventListener('click', () => {
  if (document.body.classList.contains('mobile-search-open')) {
    closeMobileSearch();
  } else {
    openMobileSearch();
  }
});

// Header search button (mobile)
document.getElementById('header-search-btn').addEventListener('click', () => {
  if (document.body.classList.contains('mobile-search-open')) {
    closeMobileSearch();
    updateBottomNav(parseRoute());
  } else {
    openMobileSearch();
  }
});
