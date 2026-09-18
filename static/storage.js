// localStorage wrappers that never throw: storage can be unavailable (Safari
// private mode, blocked site data) or full (QuotaExceededError), and a failed
// write should never break rendering.
export function storeGet(key) {
  try { return localStorage.getItem(key); }
  catch { return null; }
}

export function storeSet(key, value) {
  try { localStorage.setItem(key, value); return true; }
  catch { return false; }
}

export function storeRemove(key) {
  try { localStorage.removeItem(key); }
  catch { /* ignore */ }
}
