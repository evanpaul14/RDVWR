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
  return cleanup(ops);
}

/** Readable segments from word ops: each run of changes becomes one removal then one
 *  addition, and an unchanged stretch between two changes that is no longer than either of
 *  them (a space, a lone "the") is folded into both, so a rewrite reads as whole phrases
 *  instead of alternating fragments. */
function cleanup(ops) {
  const runs = [];   // ['=', text] or ['~', removed, added]
  for (const [op, text] of ops) {
    const last = runs[runs.length - 1];
    if (op === '=') {
      if (last?.[0] === '=') last[1] += text;
      else runs.push(['=', text]);
    } else {
      if (last?.[0] !== '~') runs.push(['~', '', '']);
      runs[runs.length - 1][op === '-' ? 1 : 2] += text;
    }
  }
  let i = 1;
  while (i < runs.length - 1) {
    const eq = runs[i][1], before = runs[i - 1], after = runs[i + 1];
    if (runs[i][0] === '=' && eq.length <= Math.max(before[1].length, before[2].length)
        && eq.length <= Math.max(after[1].length, after[2].length)) {
      runs.splice(i - 1, 3, ['~', before[1] + eq + after[1], before[2] + eq + after[2]]);
      i = Math.max(i - 1, 1);
    } else {
      i++;
    }
  }
  const out = [];
  for (const run of runs) {
    if (run[0] === '=') out.push(['=', run[1]]);
    else for (const [op, text] of [['-', run[1]], ['+', run[2]]]) if (text) out.push([op, text]);
  }
  return out;
}

/** Share of the longer text left unchanged by `segments` (0–1); a low value means the
 *  text was rewritten rather than edited. */
export function similarity(segments) {
  let kept = 0, removed = 0, added = 0;
  for (const [op, t] of segments) {
    if (op === '=') kept += t.length; else if (op === '-') removed += t.length; else added += t.length;
  }
  const longer = Math.max(kept + removed, kept + added);
  return longer ? kept / longer : 1;
}

// Below this similarity the original is shown whole instead of as a diff.
export const REWRITE_SIMILARITY = 0.5;

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
