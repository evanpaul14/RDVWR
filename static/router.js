import { settings } from './settings.js';

const SORTS = new Set(['hot','new','top','rising','controversial','best']);

function _qs(path) {
  return new URLSearchParams(path.includes('?') ? path.split('?')[1] : location.search.slice(1));
}

export function parseRoute(path=location.pathname) {
  const pathname = path.split('?')[0];
  const mDupes = pathname.match(/^\/r\/([^\/]+)\/duplicates\/([^\/]+)/i);
  if (mDupes) {
    const params = _qs(path);
    return { type: 'duplicates', sub: mDupes[1], postId: mDupes[2], after: params.get('after') || null, page: parseInt(params.get('page')) || 1 };
  }
  const mWiki = pathname.match(/^\/r\/([^\/]+)\/wiki(?:\/(.+))?/i);
  if (mWiki) return { type: 'wiki', sub: mWiki[1], page: mWiki[2] || 'index' };
  const mPost = pathname.match(/^\/r\/([^\/]+)\/comments\/([^\/]+)(?:\/[^\/]*(?:\/([a-z0-9]+))?)?/i);
  if (mPost) return { type:'post', sub:mPost[1], postId:mPost[2], commentId:mPost[3]||'' };
  const mHome = pathname.match(/^\/home(?:\/([^\/]+))?$/i);
  if (mHome) {
    const sort = SORTS.has(mHome[1]) ? mHome[1] : 'best';
    const params = _qs(path);
    return { type: 'home', sort, time: params.get('t') || 'all', after: params.get('after') || null };
  }
  const mSub  = pathname.match(/^\/r\/([^\/]+)(?:\/([^\/]+))?/);
  if (mSub) {
    const sort = SORTS.has(mSub[2]) ? mSub[2] : (mSub[1].toLowerCase() === 'popular' ? 'hot' : settings.subSort);
    const params = _qs(path);
    return { type:'sub', sub:mSub[1], sort, time: params.get('t') || settings.subTime, after: params.get('after') || null, page: parseInt(params.get('page')) || 1 };
  }
  const mMulti = pathname.match(/^\/u(?:ser)?\/([^\/]+)\/m\/([^\/]+)(?:\/([^\/]+))?/i);
  if (mMulti) {
    const sort = SORTS.has(mMulti[3]) ? mMulti[3] : 'hot';
    const params = _qs(path);
    return { type: 'multi', username: mMulti[1], multiname: mMulti[2], sort, time: params.get('t') || (sort === 'top' || sort === 'controversial' ? 'day' : 'all'), after: params.get('after') || null, page: parseInt(params.get('page')) || 1 };
  }
  const mLive = pathname.match(/^\/live\/([A-Za-z0-9_-]+)/i);
  if (mLive) return { type: 'live', threadId: mLive[1] };
  const mUser = pathname.match(/^\/u(?:ser)?\/([^\/]+)/);
  if (mUser) {
    const params = _qs(path);
    return { type:'user', username:mUser[1], after: params.get('after') || null, page: parseInt(params.get('page')) || 1 };
  }
  if (pathname === '/search') {
    const params = _qs(path);
    const q = params.get('q') || '';
    if (q) return { type:'search', query:q, sort:params.get('sort')||'relevance', time:params.get('t')||'all', sub:params.get('sub')||'', stype:params.get('stype')||'posts', after: params.get('after') || null, page: parseInt(params.get('page')) || 1 };
  }
  return { type:'home' };
}
