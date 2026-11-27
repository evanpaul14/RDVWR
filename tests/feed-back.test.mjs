// Back from a post opened over a feed reuses the loaded feed instead of reloading it.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const els = new Map(), scrolls = [], listeners = {}, calls = [];
const el = id => {
  if (!els.has(id)) els.set(id, { style: {}, scrollTop: 0, querySelector: () => null });
  return els.get(id);
};
let feedHasPosts = true;
el('feed').querySelector = sel => (sel === '.post' && feedHasPosts ? {} : null);
global.document = { getElementById: el, querySelectorAll: () => [], title: '' };
global.location = { pathname: '/', search: '', href: '/' };
global.history = {
  state: null,
  replaceState(s, _t, u) { this.state = s; if (u) setUrl(u); },
  pushState(s, _t, u) { this.state = s; setUrl(u); },
};
function setUrl(u) {
  const [p, q] = String(u).split('?');
  location.pathname = p; location.search = q ? `?${q}` : ''; location.href = u;
}
global.window = {
  scrollY: 0,
  addEventListener: (type, fn) => { listeners[type] = fn; },
  scrollTo: v => scrolls.push(v.top),
};

const mod = s => 'data:text/javascript,' + encodeURIComponent(s);
const url = p => new URL(p, import.meta.url).href;
let source = await readFile(new URL('../static/nav.js', import.meta.url), 'utf8');
const stubs = {
  './state.js': url('../static/state.js'),
  './account.js': mod('export const leaveAccount=()=>{}, loadAccount=async()=>{};'),
  './visited-ui.js': mod('export const _markPostVisited=()=>{};'),
  './utils.js': mod('export const appPath=x=>x;'),
  './router.js': mod(`export function parseRoute(p=location.pathname+location.search){
    if (p.includes('/comments/')) return {type:'post', sub:'pics', postId:'abc'};
    if (p.startsWith('/search')) return {type:'search', query:'cats', sort:'new', time:'all', sub:'', stype:'posts'};
    return {type:'sub', sub:'pics', sort:p.includes('/new')?'new':'hot', time:'all', after:null};
  }`),
  './home-mode.js': mod('export const popularIsHome=()=>false, subscribedIsHome=()=>false;'),
  './feed.js': mod(`import { state } from ${JSON.stringify(url('../static/state.js'))};
    export const loadSubreddit=async(sub,sort)=>{ globalThis.__calls.push('sub:'+sort); state.feedGen++; state.currentSub=sub; state.currentSort=sort; state.currentTime='all'; state.currentAfter=null; },
      loadMultireddit=async()=>{}, loadHome=async()=>{}, loadDuplicatesPage=async()=>{}, loadSaved=async()=>{}, loadSubscribed=async()=>{};`),
  './profile.js': mod('export const loadProfile=async()=>{};'),
  './search.js': mod(`import { state } from ${JSON.stringify(url('../static/state.js'))};
    export const searchTypeBar={style:{}};
    export const loadSearch=async()=>{ globalThis.__calls.push('search'); state.feedGen++; state.searchMode=true; };`),
  './wiki.js': mod('export const loadWikiPage=async()=>{};'),
  './live.js': mod('export const loadLiveThread=async()=>{}, cancelLivePoll=()=>{};'),
  './postview.js': mod(`export const loadPostView=async()=>{ globalThis.__calls.push('post'); }, closePostView=()=>{ globalThis.__calls.push('close'); };`),
  './sidebar.js': mod('export const closeSidebar=()=>{};'),
};
for (const [from, to] of Object.entries(stubs)) source = source.replaceAll(`'${from}'`, JSON.stringify(to));
globalThis.__calls = calls;
const { state } = await import(url('../static/state.js'));
const { navigate, renderRoute } = await import(mod(source));
const back = async () => { await listeners.popstate({ state: history.state }); };

beforeEach(() => { calls.length = 0; scrolls.length = 0; feedHasPosts = true; state.searchMode = false; });

test('back from a post restores the feed with its sort and scroll without reloading', async () => {
  setUrl('/r/pics/new');
  await renderRoute({ type: 'sub', sub: 'pics', sort: 'new', time: 'all', after: null });
  document.title = 'r/pics — RDVWR';
  window.scrollY = 4321;
  navigate('/r/pics/comments/abc');
  await new Promise(r => setImmediate(r));
  history.state = { scrollY: 4321 };
  setUrl('/r/pics/new');
  calls.length = 0;
  await back();
  assert.deepEqual(calls, ['close']);
  assert.equal(scrolls.at(-1), 4321);
  assert.equal(document.title, 'r/pics — RDVWR');
});

test('search results survive a post visit', async () => {
  setUrl('/search?q=cats&sort=new');
  await renderRoute({ type: 'search', query: 'cats', sort: 'new', time: 'all', sub: '', stype: 'posts' });
  window.scrollY = 900;
  navigate('/r/pics/comments/abc');
  await new Promise(r => setImmediate(r));
  history.state = { scrollY: 900 };
  setUrl('/search?q=cats&sort=new');
  calls.length = 0;
  await back();
  assert.deepEqual(calls, ['close']);
  assert.equal(scrolls.at(-1), 900);
});

test('a feed replaced since the post opened is loaded again', async () => {
  setUrl('/search?q=cats&sort=new');
  await renderRoute({ type: 'search', query: 'cats', sort: 'new', time: 'all', sub: '', stype: 'posts' });
  navigate('/r/pics/comments/abc');
  await new Promise(r => setImmediate(r));
  state.feedGen++; // something re-rendered the feed underneath
  setUrl('/search?q=cats&sort=new');
  calls.length = 0;
  await back();
  assert.ok(calls.includes('search'));
});
