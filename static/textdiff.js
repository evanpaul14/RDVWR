// Word-level diff of two texts, for showing what an edit changed. Mirrors core/textdiff.py
// (same tokens and algorithm, so the JS app and the no-JS page show the same diff).

const TOKEN_RE = /\s+|[^\s]+/g;
// Above this many LCS table cells the changed middle is shown as one removal + one addition.
export const MAX_CELLS = 2_000_000;

/** [[op, text], …] turning `oldText` into `newText`: op "=" (unchanged), "-" (removed) or
 *  "+" (added). Adjacent pieces with the same op are merged; leading and trailing
 *  whitespace is ignored. */
export function wordDiff(oldText, newText) {
  const a = (oldText || '').trim().match(TOKEN_RE) || [];
  const b = (newText || '').trim().match(TOKEN_RE) || [];
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length, endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  const ops = [
    ...a.slice(0, start).map(t => ['=', t]),
    ...middle(a.slice(start, endA), b.slice(start, endB)),
    ...a.slice(endA).map(t => ['=', t]),
  ];
  const out = [];
  for (const [op, text] of ops) {
    if (out.length && out[out.length - 1][0] === op) out[out.length - 1][1] += text;
    else out.push([op, text]);
  }
  return out;
}

function middle(a, b) {
  const n = a.length, m = b.length;
  if (!n || !m || n * m > MAX_CELLS) return [...a.map(t => ['-', t]), ...b.map(t => ['+', t])];
  // lcs[i*(m+1)+j]: LCS length of a[i:] and b[j:]
  const w = m + 1;
  const lcs = new Uint32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i*w + j] = a[i] === b[j] ? lcs[(i+1)*w + j + 1] + 1 : Math.max(lcs[(i+1)*w + j], lcs[i*w + j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { ops.push(['=', a[i]]); i++; j++; }
    else if (lcs[(i+1)*w + j] >= lcs[i*w + j + 1]) { ops.push(['-', a[i]]); i++; }
    else { ops.push(['+', b[j]]); j++; }
  }
  for (; i < n; i++) ops.push(['-', a[i]]);
  for (; j < m; j++) ops.push(['+', b[j]]);
  return ops;
}
