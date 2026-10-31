import { test } from 'node:test';
import assert from 'node:assert/strict';
import { streamHomePages } from '../static/home-pagination.js';

test('first results publish while the next page is pending', async () => {
  let release;
  const second = new Promise(resolve => { release = resolve; });
  const shown = [], cursors = [];
  let calls = 0;
  const running = streamHomePages({ after: null, distance: 4, isCurrent: () => true,
    fetchPage: async () => ++calls === 1 ? { posts: ['first'], after: 'next' } : second,
    filterPage: posts => posts, onPage: (posts, after) => { shown.push(...posts); cursors.push(after); },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(shown, ['first']);
  assert.deepEqual(cursors, ['next']);
  release({ posts: ['second'], after: null });
  await running;
  assert.deepEqual(shown, ['first', 'second']);
});

test('stale navigation never publishes or advances the cursor', async () => {
  let writes = 0;
  await streamHomePages({ isCurrent: () => false,
    fetchPage: async () => ({ posts: ['stale'], after: 'next' }),
    filterPage: p => p, onPage: () => writes++, after: null, distance: 4 });
  assert.equal(writes, 0);
});

test('later failures preserve published results and page limits are bounded', async () => {
  const shown = [];
  let calls = 0;
  await assert.rejects(streamHomePages({ isCurrent: () => true,
    fetchPage: async () => { if (++calls === 2) throw Error('failed'); return { posts: ['first'], after: 'next' }; },
    filterPage: p => p, onPage: p => shown.push(...p), after: null, distance: 4 }), /failed/);
  assert.deepEqual(shown, ['first']);
  calls = 0;
  await streamHomePages({ isCurrent: () => true,
    fetchPage: async () => { calls++; return { posts: [], after: 'next' }; },
    filterPage: p => p, onPage: () => {}, after: null, distance: 4 });
  assert.equal(calls, 4);
});
