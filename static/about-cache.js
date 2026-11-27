// Subreddit /about payloads seen this session (the feed header's fetch or the server's
// hydrated copy), shared so the sidebar doesn't fetch what the header already has.
const _cache = new Map();
const TTL = 5 * 60 * 1000;

export function getAbout(sub) {
  const hit = _cache.get(sub.toLowerCase());
  return hit && Date.now() - hit.ts < TTL ? hit.d : null;
}

export function setAbout(sub, d) {
  _cache.set(sub.toLowerCase(), { d, ts: Date.now() });
}
