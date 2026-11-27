// Download links for media on hosts /api/download accepts.
import { realMediaUrl, BASE } from './utils.js';

const DL_HOSTS = new Set(['v.redd.it','i.redd.it','preview.redd.it','external-preview.redd.it','i.imgur.com']);
export function dlOk(url) {
  if (!url) return false;
  try { return DL_HOSTS.has(new URL(realMediaUrl(url)).hostname); } catch { return false; }
}
export function dlHref(url, filename) {
  return `${BASE}/api/download?url=${encodeURIComponent(realMediaUrl(url))}&filename=${encodeURIComponent(filename)}`;
}
export function dlFilename(url) {
  try { return new URL(realMediaUrl(url)).pathname.split('/').filter(Boolean).pop() || 'media'; }
  catch { return 'media'; }
}
export function dlFilenamePos(url, pos) {
  const name = dlFilename(url);
  const dot = name.lastIndexOf('.');
  return dot > 0 ? `${name.slice(0, dot)}-${pos}${name.slice(dot)}` : `${name}-${pos}`;
}
export const DL_ICON = `<svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M8 2v8M5 7l3 3 3-3M3 13h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
