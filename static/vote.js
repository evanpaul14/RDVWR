// Voting as the logged-in Reddit account (see reddit_login.py). The controls only exist
// when the server says we're logged in (window.__REDDIT_LOGIN__.username), which it does
// only for requests from the server's own machine.
export const votingEnabled = () => !!window.__REDDIT_LOGIN__?.username;

const dirOf = likes => likes === true ? 1 : likes === false ? -1 : 0;

/** The vote arrows around a score. `scoreHtml` is the number's element (needs class vote-score). */
export function voteCtlHtml(fullname, likes, score, scoreHtml, cls = '') {
  const dir = dirOf(likes);
  const btn = (v, glyph, label) =>
    `<button type="button" class="vote-btn${dir === v ? ' on' : ''}" data-vote="${v}" aria-label="${label}" aria-pressed="${dir === v}">${glyph}</button>`;
  return `<span class="vote-ctl ${cls}" data-vote-id="${fullname}" data-dir="${dir}" data-score="${score || 0}">${btn(1, '▲', 'Upvote')}${scoreHtml}${btn(-1, '▼', 'Downvote')}</span>`;
}

function paint(ctl, dir) {
  ctl.dataset.dir = dir;
  ctl.querySelectorAll('.vote-btn').forEach(b => {
    const on = +b.dataset.vote === dir;
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
  });
}

async function castVote(btn, fmt) {
  const ctl = btn.closest('.vote-ctl');
  if (!ctl || ctl.dataset.busy) return;
  const old = +ctl.dataset.dir;
  const next = old === +btn.dataset.vote ? 0 : +btn.dataset.vote;
  const score = +ctl.dataset.score;
  const show = (dir, base) => {
    paint(ctl, dir);
    ctl.dataset.score = base + dir - old;
    const el = ctl.querySelector('.vote-score');
    if (el) el.textContent = fmt(+ctl.dataset.score);
  };
  show(next, score);
  ctl.dataset.busy = '1';
  try {
    const res = await fetch('/api/vote', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: ctl.dataset.voteId, dir: next }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  } catch (err) {
    show(old, score);
    ctl.title = `Vote failed: ${err.message}`;
  } finally {
    delete ctl.dataset.busy;
  }
}

/** One capture-phase listener so arrows work in feed cards, the post view and comments
 *  without triggering the card/comment click handlers underneath. */
export function initVotes(fmt) {
  document.addEventListener('click', e => {
    const btn = e.target.closest('.vote-btn[data-vote]');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    castVote(btn, fmt);
  }, true);
}
