// Own resources by DOM node so removing a view also releases its players and observers.
const resources = new WeakMap();
const observations = new WeakMap();
const sleeping = new WeakSet();
const disposed = new WeakSet();

function marked(node, set) {
  for (let el = node; el; el = el.parentNode) if (set.has(el)) return true;
  return false;
}
export const mediaDisposed = node => marked(node, disposed);
export const mediaSleeping = node => marked(node, sleeping);

export function ownMedia(node, resource) {
  if (mediaDisposed(node)) { resource.dispose?.(); return; }
  let owned = resources.get(node);
  if (!owned) resources.set(node, owned = new Set());
  owned.add(resource);
  if (mediaSleeping(node)) resource.suspend?.();
}

export function observeMedia(observer, node) {
  if (mediaDisposed(node)) return;
  let observers = observations.get(node);
  if (!observers) observations.set(node, observers = new Set());
  observers.add(observer);
  if (!mediaSleeping(node)) observer.observe(node);
}

export function unobserveMedia(observer, node) {
  observer.unobserve(node);
  observations.get(node)?.delete(observer);
}

function nodes(root) { return [root, ...root.querySelectorAll('*')]; }

export function suspendMedia(root) {
  for (const node of nodes(root)) {
    sleeping.add(node);
    observations.get(node)?.forEach(observer => observer.unobserve(node));
    resources.get(node)?.forEach(resource => resource.suspend?.());
  }
}

export function resumeMedia(root) {
  for (const node of nodes(root)) {
    sleeping.delete(node);
    observations.get(node)?.forEach(observer => observer.observe(node));
    resources.get(node)?.forEach(resource => resource.resume?.());
  }
}

export function disposeMedia(root) {
  for (const node of nodes(root)) {
    disposed.add(node);
    sleeping.delete(node);
    observations.get(node)?.forEach(observer => observer.unobserve(node));
    observations.delete(node);
    resources.get(node)?.forEach(resource => resource.dispose?.());
    resources.delete(node);
  }
}

export function initMediaCleanup(root) {
  const observer = new MutationObserver(records => {
    for (const record of records) for (const node of record.removedNodes) {
      // Moves within a view and deliberately hibernated fragments remain live.
      if (node.nodeType === 1 && !node.isConnected && !mediaSleeping(node)) disposeMedia(node);
    }
  });
  observer.observe(root, { childList: true, subtree: true });
  return observer;
}
