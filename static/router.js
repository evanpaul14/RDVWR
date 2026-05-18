const SORTS = new Set(['hot','new','top','rising','controversial','best']);

export function parseRoute(path=location.pathname) {
  const pathname = path.split('?')[0];
  const mDupes = pathname.match(/^\/r\/([^\/]+)\/duplicates\/([^\/]+)/i);
  if (mDupes) return { type: 'duplicates', sub: mDupes[1], postId: mDupes[2] };
  const mWiki = pathname.match(/^\/r\/([^\/]+)\/wiki(?:\/(.+))?/i);
  if (mWiki) return { type: 'wiki', sub: mWiki[1], page: mWiki[2] || 'index' };
  const mPost = pathname.match(/^\/r\/([^\/]+)\/comments\/([^\/]+)(?:\/[^\/]*(?:\/([a-z0-9]+))?)?/i);
  if (mPost) return { type:'post', sub:mPost[1], postId:mPost[2], commentId:mPost[3]||'' };
  const mSub  = pathname.match(/^\/r\/([^\/]+)(?:\/([^\/]+))?/);
  if (mSub) {
    const sort = SORTS.has(mSub[2]) ? mSub[2] : (mSub[1].toLowerCase() === 'popular' ? 'hot' : 'top');
    const qs = path.includes('?') ? path.split('?')[1] : location.search.slice(1);
    const time = new URLSearchParams(qs).get('t') || (sort === 'top' || sort === 'controversial' ? 'day' : 'all');
    return { type:'sub', sub:mSub[1], sort, time };
  }
  const mMulti = pathname.match(/^\/u(?:ser)?\/([^\/]+)\/m\/([^\/]+)(?:\/([^\/]+))?/i);
  if (mMulti) {
    const sort = SORTS.has(mMulti[3]) ? mMulti[3] : 'hot';
    const qs = path.includes('?') ? path.split('?')[1] : location.search.slice(1);
    const time = new URLSearchParams(qs).get('t') || (sort === 'top' || sort === 'controversial' ? 'day' : 'all');
    return { type: 'multi', username: mMulti[1], multiname: mMulti[2], sort, time };
  }
  const mUser = pathname.match(/^\/u(?:ser)?\/([^\/]+)/);
  if (mUser) return { type:'user', username:mUser[1] };
  if (pathname === '/search') {
    const qs = path.includes('?') ? path.split('?')[1] : location.search.slice(1);
    const params = new URLSearchParams(qs);
    const q = params.get('q') || '';
    if (q) return { type:'search', query:q, sort:params.get('sort')||'relevance', time:params.get('t')||'all', sub:params.get('sub')||'', nsfw:params.get('nsfw')!=='0', stype:params.get('stype')||'posts' };
  }
  return { type:'home' };
}
