import { accountActive } from './vote.js';
import { getSubs, isSubscribed, isSubscribable, toggleSub, MAX_SUBS } from './subscriptions.js';
import { onRoute } from './nav.js';

// Subscribe / unsubscribe. Local list by default; while logged in to Reddit (localhost
// only) the button joins/leaves the real subreddit instead.
const ctxSubBtn = document.getElementById('ctx-sub-btn');
const remoteSubs = new Map();  // lower-cased sub -> joined?, for the logged-in account
function _renderSubscribeBtn(sub) {
  const acct = accountActive();
  const on = acct ? !!remoteSubs.get(sub.toLowerCase()) : isSubscribed(sub);
  ctxSubBtn.textContent = acct ? (on ? 'joined' : 'join') : (on ? 'subscribed' : 'subscribe');
  ctxSubBtn.classList.toggle('is-subscribed', on);
  ctxSubBtn.setAttribute('aria-pressed', String(on));
  ctxSubBtn.title = acct ? (on ? `Leave r/${sub} on Reddit` : `Join r/${sub} on Reddit`)
    : (on ? `Unsubscribe from r/${sub}` : `Add r/${sub} to your subscribed feed`);
}
async function _loadRemoteSub(sub) {
  try {
    const d = await (await fetch(`/api/r/${encodeURIComponent(sub)}/about`)).json();
    remoteSubs.set(sub.toLowerCase(), !!d.user_is_subscriber);
  } catch {}
  if (ctxSubBtn.dataset.sub === sub) _renderSubscribeBtn(sub);
}
function updateSubscribeBtn(route) {
  const show = route.type === 'sub' && isSubscribable(route.sub);
  ctxSubBtn.hidden = !show;
  if (!show) return;
  ctxSubBtn.dataset.sub = route.sub;
  _renderSubscribeBtn(route.sub);
  if (accountActive() && !remoteSubs.has(route.sub.toLowerCase())) _loadRemoteSub(route.sub);
}
ctxSubBtn.addEventListener('click', async () => {
  const sub = ctxSubBtn.dataset.sub;
  if (!sub) return;
  if (accountActive()) {
    const join = !remoteSubs.get(sub.toLowerCase());
    try {
      const res = await fetch('/api/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sub, action: join ? 'sub' : 'unsub' }) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
      remoteSubs.set(sub.toLowerCase(), join);
      _renderSubscribeBtn(sub);
    } catch (err) {
      ctxSubBtn.title = `Failed: ${err.message}`;
    }
    return;
  }
  if (toggleSub(sub) === null) {
    ctxSubBtn.title = getSubs().length >= MAX_SUBS
      ? `You can subscribe to at most ${MAX_SUBS} subreddits`
      : 'Could not save (storage full or unavailable)';
    return;
  }
  _renderSubscribeBtn(sub);
});

onRoute(route => { if (route.type !== 'post') updateSubscribeBtn(route); });
