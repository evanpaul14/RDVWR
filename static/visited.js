const KEY = 'rdvwr_visited';
const MAX = 2000;

function _load() {
  try { return new Set(JSON.parse(localStorage.getItem(KEY) || '[]')); }
  catch { return new Set(); }
}

let _visited = _load();

export function markVisited(id) {
  if (!id || _visited.has(id)) return false;
  _visited.add(id);
  if (_visited.size > MAX) {
    const arr = [..._visited];
    _visited = new Set(arr.slice(arr.length - MAX));
  }
  localStorage.setItem(KEY, JSON.stringify([..._visited]));
  return true;
}

export function isVisited(id) { return Boolean(id && _visited.has(id)); }

export function clearVisited() {
  _visited.clear();
  localStorage.removeItem(KEY);
}
