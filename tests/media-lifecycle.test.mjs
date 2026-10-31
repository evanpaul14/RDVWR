import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ownMedia, observeMedia, unobserveMedia, suspendMedia, resumeMedia, disposeMedia,
  mediaDisposed, initMediaCleanup } from '../static/media-lifecycle.js';

function node(children = []) {
  const result = { children, nodeType: 1, isConnected: false,
    querySelectorAll: () => children.flatMap(child => [child, ...child.querySelectorAll('*')]) };
  children.forEach(child => { child.parentNode = result; });
  return result;
}
function observer() {
  const observed = new Set();
  return { observed, observe: el => observed.add(el), unobserve: el => observed.delete(el) };
}

test('hibernation stops resources and observers; wake restores observation without disposal', () => {
  const video = node(), card = node([video]), io = observer();
  let paused = 0, destroyed = 0;
  ownMedia(video, { suspend: () => paused++, dispose: () => destroyed++ });
  observeMedia(io, video);
  suspendMedia(card);
  assert.equal(paused, 1);
  assert.equal(io.observed.size, 0);
  resumeMedia(card);
  assert.equal(io.observed.has(video), true);
  assert.equal(destroyed, 0);
  disposeMedia(card);
  disposeMedia(card);
  assert.equal(destroyed, 1);
  assert.equal(io.observed.size, 0);
});

test('finished one-shot observations stay finished after waking', () => {
  const target = node(), io = observer();
  observeMedia(io, target);
  unobserveMedia(io, target);
  suspendMedia(target);
  resumeMedia(target);
  assert.equal(io.observed.size, 0);
});

test('late async resources are disposed; late sleeping resources are suspended', () => {
  const target = node();
  let paused = 0, destroyed = 0;
  suspendMedia(target);
  ownMedia(target, { suspend: () => paused++, dispose: () => destroyed++ });
  assert.equal(paused, 1);
  disposeMedia(target);
  ownMedia(target, { dispose: () => destroyed++ });
  assert.equal(destroyed, 2);
});

test('removed views are disposed but moves and hibernation are preserved', () => {
  let callback;
  global.MutationObserver = class { constructor(fn) { callback = fn; } observe() {} };
  initMediaCleanup(node());
  const removed = node(), moved = node(), asleep = node();
  moved.isConnected = true;
  suspendMedia(asleep);
  callback([{ removedNodes: [removed, moved, asleep] }]);
  assert.equal(mediaDisposed(removed), true);
  assert.equal(mediaDisposed(moved), false);
  assert.equal(mediaDisposed(asleep), false);
});
