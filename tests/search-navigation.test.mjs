import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Run the real UI modules with lightweight DOM/dependency doubles.
function harness(file, overrides = {}) {
  const nodes = new Map();
  const element = id => {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, { id, value: '', innerHTML: '', textContent: '', style: {},
        listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; },
        classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) },
      });
    }
    return nodes.get(id);
  };
  const context = vm.createContext({
    document: { getElementById: element, body: element('body') }, window: {},
    state: { feedGen: 0, currentSub: '' }, settings: { subSort: 'top' },
    navigationGuard: () => () => true,
    accountActive: () => false, onRoute: () => {}, hideAllAutocomplete: () => {},
    escHtml: s => s, fmtNum: n => n, setTimeout, ...overrides,
  });
  const source = readFileSync(new URL(`../static/${file}`, import.meta.url), 'utf8')
    .replace(/^import\s[\s\S]*?from\s+'[^']+';\n/gm, '')
    .replace(/^export /gm, '');
  vm.runInContext(source, context);
  return { context, element };
}

for (const id of ['subreddit-input', 'pv-subreddit-input', 'mobile-search-input']) {
  test(`Enter in ${id} navigates once and consumes the key before focus changes`, () => {
    const paths = [];
    const { context, element } = harness('search-box.js', { navigate: path => paths.push(path) });
    const input = element(id);
    input.value = 'r/python';
    context.document.activeElement = input;
    let prevented = false, stopped = false;
    input.listeners.keydown({ key: 'Enter', target: input, currentTarget: input,
      preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
    assert.deepEqual(paths, ['/r/python/top']);
    assert.equal(prevented, true);
    assert.equal(stopped, true);
  });
}

for (const destination of ['home', 'another subreddit', 'same subreddit revisited']) {
  test(`late subreddit metadata cannot overwrite ${destination}`, async () => {
    let release;
    const response = new Promise(resolve => { release = resolve; });
    const { context, element } = harness('feed.js', { fetch: () => response });
    context.state.currentSub = 'python';
    const pending = context.loadAbout('python');
    context.state.feedGen++;
    context.state.currentSub = destination === 'home' ? '' : destination === 'another subreddit' ? 'javascript' : 'python';
    element('ctx-title').textContent = 'Current page';
    release({ ok: true, json: async () => ({ title: 'Old Python title', subscribers: 5 }) });
    await pending;
    assert.equal(element('ctx-title').textContent, 'Current page');
    assert.equal(element('ctx-info').classList.contains('visible'), false);
  });
}

test('current subreddit metadata still renders', async () => {
  const { context, element } = harness('feed.js', {
    fetch: async () => ({ ok: true, json: async () => ({ title: 'Python', subscribers: 5 }) }),
  });
  context.state.currentSub = 'Python';
  await context.loadAbout('Python');
  assert.equal(element('ctx-title').textContent, 'Python');
  assert.equal(element('ctx-info').classList.contains('visible'), true);
});
