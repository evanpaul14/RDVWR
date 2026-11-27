"""Word-level diff of two texts, for showing what an edit changed. Mirrored by
static/textdiff.js (same tokens and algorithm, so both sides show the same diff)."""
import re

_TOKEN_RE = re.compile(r"\s+|[^\s]+")

# Above this many LCS table cells the changed middle is shown as one removal + one addition.
MAX_CELLS = 2_000_000


def word_diff(old, new):
    """[[op, text], …] turning `old` into `new`: op "=" (unchanged), "-" (removed) or
    "+" (added). Adjacent pieces with the same op are merged; leading and trailing
    whitespace is ignored."""
    a = _TOKEN_RE.findall((old or "").strip())
    b = _TOKEN_RE.findall((new or "").strip())
    start = 0
    while start < len(a) and start < len(b) and a[start] == b[start]:
        start += 1
    end_a, end_b = len(a), len(b)
    while end_a > start and end_b > start and a[end_a - 1] == b[end_b - 1]:
        end_a -= 1
        end_b -= 1
    ops = [("=", t) for t in a[:start]]
    ops += _middle(a[start:end_a], b[start:end_b])
    ops += [("=", t) for t in a[end_a:]]
    return _cleanup(ops)


def _cleanup(ops):
    """Readable segments from word ops: each run of changes becomes one removal then one
    addition, and an unchanged stretch between two changes that is no longer than either of
    them (a space, a lone "the") is folded into both, so a rewrite reads as whole phrases
    instead of alternating fragments."""
    runs = []   # ["=", text] or ["~", removed, added]
    for op, text in ops:
        if op == "=":
            if runs and runs[-1][0] == "=":
                runs[-1][1] += text
            else:
                runs.append(["=", text])
        else:
            if not runs or runs[-1][0] != "~":
                runs.append(["~", "", ""])
            runs[-1][1 if op == "-" else 2] += text
    i = 1
    while i < len(runs) - 1:
        eq, before, after = runs[i][1], runs[i - 1], runs[i + 1]
        if (runs[i][0] == "=" and len(eq) <= max(len(before[1]), len(before[2]))
                and len(eq) <= max(len(after[1]), len(after[2]))):
            runs[i - 1:i + 2] = [["~", before[1] + eq + after[1], before[2] + eq + after[2]]]
            i = max(i - 1, 1)
        else:
            i += 1
    out = []
    for run in runs:
        if run[0] == "=":
            out.append(["=", run[1]])
        else:
            out += [[op, text] for op, text in (("-", run[1]), ("+", run[2])) if text]
    return out


def similarity(segments):
    """Share of the longer text left unchanged by `segments` (0–1); a low value means the
    text was rewritten rather than edited."""
    kept = sum(len(t) for op, t in segments if op == "=")
    old = kept + sum(len(t) for op, t in segments if op == "-")
    new = kept + sum(len(t) for op, t in segments if op == "+")
    return kept / max(old, new) if max(old, new) else 1.0


# Below this similarity the original is shown whole instead of as a diff.
REWRITE_SIMILARITY = 0.5


def _middle(a, b):
    n, m = len(a), len(b)
    if not n or not m or n * m > MAX_CELLS:
        return [("-", t) for t in a] + [("+", t) for t in b]
    # lcs[i][j]: LCS length of a[i:] and b[j:]
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, below = lcs[i], lcs[i + 1]
        for j in range(m - 1, -1, -1):
            row[j] = below[j + 1] + 1 if a[i] == b[j] else max(below[j], row[j + 1])
    ops, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            ops.append(("=", a[i]))
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            ops.append(("-", a[i]))
            i += 1
        else:
            ops.append(("+", b[j]))
            j += 1
    ops += [("-", t) for t in a[i:]] + [("+", t) for t in b[j:]]
    return ops
