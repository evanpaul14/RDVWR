import { test } from 'node:test';
import assert from 'node:assert/strict';
import { takeInitialListing } from '../static/hydration.js';

const params = { sort: 'hot', after: '', mode: null };
function seed(account = null) {
  global.window = { __INITIAL_LISTING__: { kind: 'home', params, account, data: { posts: [], after: 'next' } } };
}
test('matching hydration is consumed exactly once', () => {
  seed();
  assert.equal(takeInitialListing('home', { ...params }).after, 'next');
  assert.equal(takeInitialListing('home', params), null);
});
test('different cursors, filters, sources and accounts reject hydration', () => {
  for (const change of [{ after: 'next' }, { sort: 'new' }, { mode: 'cookies' }]) {
    seed();
    assert.equal(takeInitialListing('home', { ...params, ...change }), null);
  }
  seed('someone');
  assert.equal(takeInitialListing('home', params), null);
  seed();
  assert.equal(takeInitialListing('search', params), null);
});
