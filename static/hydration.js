// First-load payloads are single-use and must match the current view and account.
export function takeInitialListing(kind, params) {
  const initial = window.__INITIAL_LISTING__;
  if (!initial) return null;
  window.__INITIAL_LISTING__ = null;
  if (initial.kind !== kind || initial.account !== (window.__REDDIT_LOGIN__?.username || null)) return null;
  const expected = initial.params;
  if (Object.keys(expected).length !== Object.keys(params).length ||
      Object.entries(params).some(([key, value]) => expected[key] !== value)) return null;
  return initial.data;
}
