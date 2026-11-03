import { appPath } from './utils.js';

// The backend's /api/* same-site gate normally checks Sec-Fetch-Site/Origin/Referer,
// but some browser privacy hardening (e.g. Firefox's privacy.resistFingerprinting)
// strips all three from every request, indistinguishable server-side from a bot. Echo
// the rdvwr_csrf cookie the backend sets back as a header on every same-origin fetch()
// (CSP's connect-src 'self' means that's the only kind we ever make) as a fallback proof
// a cross-site page can't forge, since it can't read this origin's cookies either.
(() => {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const m = document.cookie.match(/(?:^|; )rdvwr_csrf=([^;]+)/);
    if (m) init = { ...init, headers: { ...(init.headers || {}), 'X-Rdvwr-Fetch': m[1] } };
    return nativeFetch(typeof input === 'string' ? appPath(input) : input, init);
  };
})();
