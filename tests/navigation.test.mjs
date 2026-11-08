// Exercise the real route/feed/profile modules with deferred network responses.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const elements = new Map(), storage = new Map(), scrolls = [];
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    innerHTML: '', textContent: '', value: '', style: {}, children: [],
    classList: { add() {}, remove() {} }, addEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
  });
  return elements.get(id);
}
global.document = { getElementById: element, title: '' };
global.window = { addEventListener() {}, scrollTo: value => scrolls.push(value), __REDDIT_LOGIN__: { username: 'alice' } };
global.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
const ui = global.__navigationTest = { settings: { layout: 'card' }, mode: 'login' };
const { state } = await import('../static/state.js');
const url = path => new URL(path, import.meta.url).href;
const moduleUrl = source => 'data:text/javascript,' + encodeURIComponent(source);
const stubs = {
  './state.js': url('../static/state.js'),
  './home-pagination.js': url('../static/home-pagination.js'),
  './hydration.js': url('../static/hydration.js'),
  './homefeed-seen.js': url('../static/homefeed-seen.js'),
  './settings.js': moduleUrl('export const settings = globalThis.__navigationTest.settings; export const personalizedHomeMode = () => globalThis.__navigationTest.mode;'),
  './utils.js': moduleUrl('export const escHtml=String, fmtNum=String, fmtDate=String, errState=String, buildTimeFilterHtml=()=>"", SKELETON_COUNT=1, openOnReddit=()=>{}, BASE="", appPath=x=>x;'),
  './render.js': moduleUrl('export const renderPost=()=>"", renderUserCommentCard=()=>"", waitForMdLibs=()=>globalThis.__navigationTest.mdReady || Promise.resolve();'),
  './media.js': moduleUrl('export const initMedia=()=>{}, initGifVideos=()=>{};'),
  './saved.js': moduleUrl('export const getSavedPosts=()=>[];'),
  './subscriptions.js': moduleUrl('export const getSubs=()=>[];'),
  './vote.js': moduleUrl('export const accountActive=()=>!!window.__REDDIT_LOGIN__?.username;'),
  './hibernate.js': moduleUrl('export const cardContent=x=>x;'),
};
async function adapted(path, imports) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [from, to] of Object.entries(imports)) source = source.replaceAll(`'${from}'`, JSON.stringify(to));
  return moduleUrl(source);
}
const feedUrl = await adapted('../static/feed.js', stubs);
const feed = await import(feedUrl);
const profileUrl = await adapted('../static/profile.js', { ...stubs, './feed.js': feedUrl });
const navUrl = await adapted('../static/nav.js', {
  ...stubs, './feed.js': feedUrl, './profile.js': profileUrl,
  './account.js': moduleUrl('export const leaveAccount=()=>{}, loadAccount=async()=>{};'),
  './visited-ui.js': moduleUrl('export const _markPostVisited=()=>{};'),
  './router.js': moduleUrl('export const parseRoute=()=>({type:"saved"});'),
  './home-mode.js': moduleUrl('export const popularIsHome=()=>false, subscribedIsHome=()=>false;'),
  './search.js': moduleUrl('export const searchTypeBar={style:{}}, loadSearch=async()=>{};'),
  './wiki.js': moduleUrl('export const loadWikiPage=async()=>{};'),
  './live.js': moduleUrl('export const loadLiveThread=async()=>{}, cancelLivePoll=()=>{};'),
  './postview.js': moduleUrl('export const loadPostView=async()=>{}, closePostView=()=>{};'),
  './sidebar.js': moduleUrl('export const closeSidebar=()=>{};'),
});
const { renderRoute } = await import(navUrl);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const response = data => ({ ok: true, json: async () => data });
const flush = () => new Promise(resolve => setImmediate(resolve));

beforeEach(() => {
  state.feedGen = state.navigationGen = 0;
  state.loading = state.subsMode = state.profileMode = false;
  state.afterToken = null;
  ui.mode = 'login';
  ui.settings.redditCookies = '';
  ui.mdReady = null;
  window.__INITIAL_PROFILE__ = null;
  window.__REDDIT_LOGIN__ = { username: 'alice' };
  storage.clear(); scrolls.length = 0;
});

test('current subscriptions still load the account feed', async () => {
  const requests = [];
  global.fetch = async path => {
    requests.push(path);
    return response(path === '/api/subscriptions' ? { subs: ['python'] } : { posts: [], after: null });
  };
  await renderRoute({ type: 'subscribed', sort: 'hot' });
  assert.deepEqual(requests, ['/api/subscriptions', '/api/subscribed?sort=hot']);
  assert.equal(state.subsMode, true);
  assert.equal(state.loading, false);
  assert.equal(document.title, 'Subscribed — RDVWR');
});

for (const failure of [false, true]) test(`older subscriptions ${failure ? 'failure' : 'response'} cannot overwrite Saved or restore old scroll`, async () => {
  const pending = deferred();
  global.fetch = () => pending.promise;
  const old = renderRoute({ type: 'subscribed', sort: 'hot' }, { restoreScroll: 999 });
  await renderRoute({ type: 'saved' });
  const scrollCount = scrolls.length;
  if (failure) pending.reject(Error('network failure'));
  else pending.resolve(response({ subs: [] }));
  await old;
  assert.equal(document.title, 'Saved — RDVWR');
  assert.equal(state.savedMode, true);
  assert.equal(state.subsMode, false);
  assert.equal(state.loading, false);
  assert.equal(scrolls.length, scrollCount);
});

test('older subreddit header cannot overwrite the current page', async () => {
  const pending = deferred();
  global.fetch = path => path.endsWith('/about') ? pending.promise : Promise.resolve(response({ posts: [], after: null }));
  await renderRoute({ type: 'sub', sub: 'oldsub', sort: 'hot' });
  await renderRoute({ type: 'saved' });
  pending.resolve(response({ title: 'Old subreddit', subscribers: 5 }));
  await flush();
  assert.equal(element('ctx-title').textContent, 'Saved posts');
});

test('older profile header cannot overwrite the current page', async () => {
  const pending = deferred();
  global.fetch = path => path.endsWith('/about') ? pending.promise : Promise.resolve(response({ items: [], after: null }));
  const old = renderRoute({ type: 'user', username: 'olduser' });
  await renderRoute({ type: 'saved' });
  pending.resolve(response({ name: 'olduser', karma_post: 3, karma_comment: 4 }));
  await old;
  assert.equal(element('ctx-title').textContent, 'Saved posts');
});

test('hydrated profile headers also respect later navigation', async () => {
  const pending = deferred();
  ui.mdReady = pending.promise;
  window.__INITIAL_PROFILE__ = { _username: 'olduser', items: [], _about: { name: 'olduser' } };
  const old = renderRoute({ type: 'user', username: 'olduser' });
  await renderRoute({ type: 'saved' });
  pending.resolve();
  await old;
  assert.equal(element('ctx-title').textContent, 'Saved posts');
});

test('current subreddit and profile headers still render', async () => {
  global.fetch = async path => response(path.endsWith('/about')
    ? { title: 'Current subreddit', name: 'currentuser', subscribers: 10 }
    : { posts: [], items: [], after: null });
  await renderRoute({ type: 'sub', sub: 'currentsub', sort: 'hot' });
  await flush();
  assert.equal(element('ctx-title').textContent, 'Current subreddit');
  await renderRoute({ type: 'user', username: 'currentuser' });
  assert.equal(element('ctx-title').textContent, 'u/currentuser');
});

for (const mode of ['login', null, 'cookies']) test(`identity changes discard pending ${mode || 'frontpage'} pages and their persisted cursors`, async () => {
  ui.mode = mode;
  ui.settings.redditCookies = 'fixture_alice';
  const pending = deferred();
  global.fetch = () => pending.promise;
  const old = feed.loadHomeFeed('best', 'all');
  await flush();
  window.__REDDIT_LOGIN__ = { username: 'bob' };
  ui.settings.redditCookies = 'fixture_bob';
  pending.resolve(response({ posts: [], after: 'alice_cursor' }));
  await old;
  assert.equal(state.afterToken, null);
  assert.equal([...storage.values()].some(value => value.includes('alice_cursor')), false);
});
