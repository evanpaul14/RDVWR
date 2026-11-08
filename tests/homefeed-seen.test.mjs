import { test } from 'node:test';
import assert from 'node:assert/strict';

const values = new Map();
global.localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
const fresh = () => import(`../static/homefeed-seen.js?instance=${Math.random()}`);

test('history resumes after reload for the same identity and resets for other sources/accounts', async () => {
  values.clear();
  const first = await fresh();
  const alice = await first.homeFeedScope('login', 'Alice');
  first.markHomeSeen(['post'], alice);
  first.setHomeCursor('best', 'all', 'alice_cursor', 12, alice);
  const reloaded = await fresh();
  assert.equal(await reloaded.homeFeedScope('login', 'alice'), alice);
  assert.equal(reloaded.isHomeSeen('post', alice), true);
  assert.equal(reloaded.getHomeCursor('best', 'all', alice).after, 'alice_cursor');
  for (const scope of [await first.homeFeedScope('login', 'Bob'), await first.homeFeedScope(null), await first.homeFeedScope(null, 'Bob')]) {
    assert.equal(reloaded.isHomeSeen('post', scope), false);
    assert.equal(reloaded.getHomeCursor('best', 'all', scope), null);
    reloaded.markHomeSeen(['different'], scope);
  }
});

test('pasted-cookie identities are isolated, stable across reloads and never stored in plaintext', async () => {
  values.clear();
  const history = await fresh();
  const cookie = 'reddit_session=fixture_secret_a';
  const alice = await history.homeFeedScope('cookies', cookie);
  const bob = await history.homeFeedScope('cookies', 'reddit_session=fixture_secret_b');
  assert.notEqual(alice, bob);
  assert.notEqual(alice, await history.homeFeedScope('login', 'Alice'));
  assert.equal(alice, await (await fresh()).homeFeedScope('cookies', cookie));
  history.markHomeSeen(['post'], alice);
  history.setHomeCursor('best', 'all', 'cursor', 8, alice);
  assert.equal(history.getHomeCursor('best', 'all', bob), null);
  assert.equal(history.isHomeSeen('post', bob), false);
  assert.equal([...values.values()].some(value => value.includes('fixture_secret')), false);
});

test('legacy unscoped history is ignored; history remains bounded and cursors expire', async () => {
  values.clear();
  values.set('rdvwr_home_seen', '["legacy"]');
  values.set('rdvwr_home_cursor', JSON.stringify({ 'best:all': { after: 'legacy', ts: Date.now() } }));
  const history = await fresh();
  const scope = await history.homeFeedScope(null);
  assert.equal(history.isHomeSeen('legacy', scope), false);
  assert.equal(history.getHomeCursor('best', 'all', scope), null);
  history.markHomeSeen(Array.from({ length: 1001 }, (_, i) => `post${i}`), scope);
  assert.equal(history.isHomeSeen('post0', scope), false);
  assert.equal(history.isHomeSeen('post1000', scope), true);
  history.setHomeCursor('top', 'day', 'cursor', 8, scope);
  assert.equal(history.getHomeCursor('top', 'week', scope), null);
  const now = Date.now;
  try {
    Date.now = () => now() + 31 * 60 * 1000;
    assert.equal(history.getHomeCursor('top', 'day', scope), null);
  } finally { Date.now = now; }
});

test('cookie-backed history stays usable without WebCrypto and resets after a reload', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  try {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: undefined });
    const history = await fresh();
    const scope = await history.homeFeedScope('cookies', 'fixture');
    assert.equal(await history.homeFeedScope('cookies', 'fixture'), scope);
    assert.notEqual(await (await fresh()).homeFeedScope('cookies', 'fixture'), scope);
  } finally { Object.defineProperty(globalThis, 'crypto', descriptor); }
});
