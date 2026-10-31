// Exercise the real media module with deterministic DOM/player adapters; no browser or network.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { suspendMedia, resumeMedia, disposeMedia } from '../static/media-lifecycle.js';

const observers = [], players = [];
global.document = { addEventListener() {}, querySelectorAll() { return []; } };
global.window = { addEventListener() {} };
global.IntersectionObserver = class {
  constructor(callback, options) { this.callback = callback; this.options = options; this.targets = new Set(); observers.push(this); }
  observe(node) { this.targets.add(node); }
  unobserve(node) { this.targets.delete(node); }
};
global.Hls = class {
  static isSupported() { return true; }
  static Events = { ERROR: 'error', MANIFEST_PARSED: 'manifest', LEVEL_SWITCHED: 'level' };
  constructor() { this.starts = 0; this.stops = 0; this.destroyed = 0; players.push(this); }
  loadSource(url) { this.url = url; }
  attachMedia(video) { this.video = video; }
  startLoad() { this.starts++; }
  stopLoad() { this.stops++; }
  destroy() { this.destroyed++; }
  on() {}
};
const stub = source => 'data:text/javascript,' + encodeURIComponent(source);
const imports = {
  './media-lifecycle.js': new URL('../static/media-lifecycle.js', import.meta.url).href,
  './state.js': stub('export const state = {}; export function setMutePref() {} export function setVolumePref() {}'),
  './settings.js': stub('export const settings = {};'),
  './utils.js': stub('export const BASE="", GALLERY_SWIPE_MIN=30; export const escHtml=x=>x, evictMap=()=>{}, renderPoll=()=>{}, veilWrap=()=>{}, realMediaUrl=x=>x, isLocalUrl=()=>true;'),
};
let source = await readFile(new URL('../static/media.js', import.meta.url), 'utf8');
for (const [path, replacement] of Object.entries(imports)) source = source.replace(`'${path}'`, JSON.stringify(replacement));
const { initVideos } = await import(stub(source));

function card() {
  const video = new EventTarget();
  Object.assign(video, { dataset: {}, querySelectorAll: () => [],
    pause() { this.dispatchEvent(new Event('pause')); }, removeAttribute() {}, load() {},
  });
  const wrap = { dataset: { hls: '/playlist.m3u8' },
    querySelector: () => video,
    querySelectorAll: selector => selector === '*' || selector === 'video' ? [video] : [],
  };
  video.parentNode = wrap;
  return { wrap, video, container: { querySelectorAll: selector => selector === 'video' ? [video] : [wrap] } };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('feed players initialize near the viewport, resume after sleep, and dispose once', async () => {
  const { wrap, video, container } = card();
  initVideos(container);
  assert.equal(players.length, 0, 'offscreen cards must not fetch manifests');
  const observer = observers.find(o => o.options.rootMargin === '300px');
  assert.equal(observer.targets.has(wrap), true);
  observer.callback([{ target: wrap, isIntersecting: true }]);
  await flush();
  assert.equal(players.length, 1);
  assert.equal(observer.targets.has(wrap), false);
  const player = players[0];
  video.dispatchEvent(new Event('play'));
  assert.equal(player.starts, 1);
  suspendMedia(wrap);
  assert.ok(player.stops > 0);
  resumeMedia(wrap);
  assert.equal(observer.targets.has(wrap), false, 'completed initialization stays unobserved');
  video.dispatchEvent(new Event('play'));
  assert.equal(player.starts, 2);
  disposeMedia(wrap);
  disposeMedia(wrap);
  assert.equal(player.destroyed, 1);
});

test('queued visibility callbacks cannot initialize removed or sleeping cards', async () => {
  for (const action of [disposeMedia, suspendMedia]) {
    const { wrap, container } = card();
    initVideos(container);
    action(wrap);
    observers.find(o => o.options.rootMargin === '300px').callback([{ target: wrap, isIntersecting: true }]);
    await flush();
    assert.equal(players.length, 1);
  }
});
