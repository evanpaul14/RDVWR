// localStorage wrappers that never throw (storage can be unavailable or full).
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
