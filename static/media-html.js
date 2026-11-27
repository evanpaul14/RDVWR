// Media markup for feed cards and the post view, per layout and embed setting.
import { settings } from './settings.js';
import { escHtml, renderPoll, veilWrap, realMediaUrl, isLocalUrl } from './utils.js';
import { galleryThumb, renderGallery } from './media-gallery.js';

// Where a post's media lives upstream, for a plain outbound link.
function _mediaLink(p) {
  const href = p.url || '';
  if (p.is_video)        return { label: 'video ↗', href: realMediaUrl(p.video_url) || href };
  if (p.youtube_id)      return { label: 'YouTube video ↗', href: `https://www.youtube.com/watch?v=${p.youtube_id}` };
  if (p.tiktok_id)       return { label: 'TikTok video ↗', href };
  if (p.imgur_album_id)  return { label: 'Imgur album ↗', href: `https://imgur.com/a/${p.imgur_album_id}` };
  if (p.streamable_id)   return { label: 'Streamable video ↗', href: `https://streamable.com/${p.streamable_id}` };
  if (p.embed_url)       return { label: 'embedded media ↗', href };
  if (p.gif_url)         return { label: 'gif ↗', href: realMediaUrl(p.gif_url) };
  if (p.gallery?.length) return { label: `gallery (${p.gallery.length}) ↗`, href };
  return { label: 'image ↗', href: realMediaUrl(href) };
}

// True when rendering this post's media inline would make the browser load it
// straight from a third party (an iframe player or an unproxied CDN URL).
function _embedsThirdParty(p, full) {
  if (p.poll || p.is_devvit || p.redgifs_id || p.imgur_album_id) return false;
  if (p.youtube_id || p.tiktok_id || p.streamable_id || p.embed_url) return true;
  if (p.is_video) return !isLocalUrl(p.hls_url || p.video_url);
  if (p.gif_url)  return !isLocalUrl(p.gif_url);
  if (p.gallery?.length) return p.gallery.some(g => !isLocalUrl(g.url));
  const img = p.is_self ? null : (full ? p.preview_img : (p.thumb_url ?? p.preview_img));
  return !!img && !isLocalUrl(img);
}

// "Don't embed third-party media" setting: such posts are shown like article links —
// a proxied preview image (if any) plus an outbound link to where the media lives.
export function linksOutMedia(p, full = false) {
  return !!settings.linkExternalMedia && _embedsThirdParty(p, full);
}
export function mediaLinkHref(p) {
  return _mediaLink(p).href;
}
export function mediaLinkDomain(p) {
  try { return new URL(_mediaLink(p).href).hostname.replace(/^www\./, ''); } catch { return p.domain || ''; }
}

function _linkedPreviewHtml(p) {
  const img = [p.preview_img, p.thumb_url].find(isLocalUrl);
  return img ? `<div class="pv-media"><img src="${escHtml(img)}" loading="lazy" alt="" data-onerror="no-media"></div>` : '';
}

// Card-embed fallback (crossposts): a proxied thumbnail (if any) plus an outbound link.
function _linkedMediaHtml(p, full) {
  const ic = full ? 'pv-media' : 'post-media';
  const thumb = [full ? p.preview_img : (p.thumb_url ?? p.preview_img), p.preview_img].find(isLocalUrl);
  const thumbHtml = thumb ? `<img src="${escHtml(thumb)}" loading="lazy" alt="">` : '';
  const { label, href } = _mediaLink(p);
  if (!href) return thumbHtml ? `<div class="${ic}">${thumbHtml}</div>` : '';
  return `<div class="${ic} minimal-media">${thumbHtml}<a class="minimal-media-link" href="${escHtml(href)}" target="_blank" rel="noopener noreferrer">${escHtml(label)}</a></div>`;
}

// ── Minimal mode: plain thumbnail + link, no video/iframe/gallery-nav embeds ──
function _minimalMediaHtml(p, full) {
  const ic = full ? 'pv-media' : 'post-media';
  const g0 = p.gallery?.[0];
  const thumb = (g0 && (full ? g0.url : galleryThumb(g0))) ?? (full ? p.preview_img : (p.thumb_url ?? p.preview_img)) ?? null;
  const thumbHtml = thumb ? `<img src="${escHtml(thumb)}" loading="lazy" alt="">` : '';
  let label = '', href = p.url || '';
  if (p.is_video)                  { label = 'video ↗'; href = p.video_url || p.url; }
  else if (p.youtube_id)           { label = 'YouTube video ↗'; href = `https://www.youtube.com/watch?v=${p.youtube_id}`; }
  else if (p.tiktok_id)            { label = 'TikTok video ↗'; }
  else if (p.redgifs_id)           { label = 'video ↗'; }
  else if (p.imgur_album_id)       { label = 'Imgur album ↗'; }
  else if (p.streamable_id)        { label = 'video ↗'; }
  else if (p.embed_url)            { label = 'embedded media ↗'; }
  else if (p.gif_url && p.gif_is_video) { label = 'gif ↗'; href = p.gif_url; }
  else if (p.gif_url)              { return `<div class="${ic}"><img src="${escHtml(p.gif_url)}" loading="lazy" alt=""></div>`; }
  else if (p.gallery?.length > 1)  {
    const imgs = p.gallery.map(img => `<img src="${escHtml(full ? img.url : galleryThumb(img))}" loading="lazy" alt="${escHtml(img.caption||'')}">`).join('');
    return `<div class="${ic} minimal-gallery-stack">${imgs}</div>`;
  }
  else if (thumb)                  { return `<div class="${ic}">${thumbHtml}</div>`; }
  else if (!p.is_self && p.url && /^https?:\/\//.test(p.url)) { label = `${(() => { try { return new URL(p.url).hostname; } catch { return 'link'; } })()} ↗`; }
  else return '';
  if (!href) return '';
  return `<div class="${ic} minimal-media">${thumbHtml}<a class="minimal-media-link" href="${escHtml(href)}" target="_blank" rel="noopener">${escHtml(label)}</a></div>`;
}

export function mediaHtml(p, full = false) {
  if (p.poll) return renderPoll(p.poll);
  const linkOnly = settings.linkExternalMedia && _embedsThirdParty(p, full);
  if (linkOnly || settings.layout === 'minimal') {
    let html = !linkOnly ? _minimalMediaHtml(p, full)
      : full && settings.layout !== 'minimal' ? _linkedPreviewHtml(p) // the post view adds the article box
      : _linkedMediaHtml(p, full);
    if (!html) return '';
    if (p.is_spoiler) html = veilWrap('spoiler', html);
    if (p.over_18)   html = veilWrap('nsfw', html);
    return html;
  }
  if (p.is_devvit) {
    const imgHtml = p.preview_img
      ? `<img class="devvit-preview" src="${escHtml(p.preview_img)}" loading="lazy" alt="">`
      : '';
    const href = escHtml(p.devvit_url || p.permalink || '#');
    const permalink = escHtml(p.permalink || '');
    return `<div class="devvit-card${full ? ' devvit-card-full' : ''}" data-permalink="${permalink}">
      ${imgHtml}
      <div class="devvit-overlay">
        <span class="devvit-badge"><svg width="14" height="14" viewBox="0 0 20 20" fill="none"><rect x="2" y="2" width="16" height="16" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M7 10h6M10 7v6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg> Interactive App</span>
        <div class="devvit-btns">
          <button class="devvit-try-btn" title="Try the interactive app">Try App</button>
          <a class="devvit-open-btn" href="${href}" target="_blank" rel="noopener noreferrer">Open on Reddit ↗</a>
        </div>
      </div>
    </div>`;
  }
  const vc = full ? 'pv-media' : 'post-video';
  const ic = full ? 'pv-media' : 'post-media';
  const preload = full ? 'metadata' : 'none';
  let html = '';
  if (p.is_video) {
    html = `<div class="${vc}" data-hls="${escHtml(p.hls_url||'')}" data-src="${escHtml(p.video_url||'')}" data-audio="${escHtml(p.audio_url||'')}"`+(p.preview_img?` data-poster="${escHtml(p.preview_img)}"`:'')+`><video controls preload="${preload}" playsinline muted></video></div>`;
  } else if (p.youtube_id) {
    html = `<div class="${vc}"><iframe src="https://www.youtube-nocookie.com/embed/${escHtml(p.youtube_id)}" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen loading="lazy"></iframe></div>`;
  } else if (p.tiktok_id) {
    html = `<div class="${vc} tiktok-wrap"><iframe src="https://www.tiktok.com/player/v1/${escHtml(p.tiktok_id)}?autoplay=0&rel=0" allowfullscreen loading="lazy" sandbox="allow-scripts allow-same-origin allow-popups"></iframe></div>`;
  } else if (p.redgifs_id) {
    html = `<div class="${vc} redgifs-wrap" data-rgid="${escHtml(p.redgifs_id)}" data-fb-hls="${escHtml(p.redgifs_fallback_hls||'')}" data-fb-src="${escHtml(p.redgifs_fallback_url||'')}"><div class="rg-loading"></div></div>`;
  } else if (p.imgur_album_id) {
    html = `<div class="${vc} imgur-album-wrap" data-iaid="${escHtml(p.imgur_album_id)}"><div class="rg-loading"></div></div>`;
  } else if (p.streamable_id) {
    html = `<div class="${vc}"><div class="streamable-embed"><iframe src="https://streamable.com/e/${escHtml(p.streamable_id)}" frameborder="0" width="100%" height="100%" allowfullscreen allow="autoplay"></iframe></div></div>`;
  } else if (p.embed_url) {
    // Tweets: load Twitter's own dark-themed embed; Reddit's wrapper page is always light.
    const src = p.tweet_id
      ? `https://platform.twitter.com/embed/Tweet.html?id=${escHtml(p.tweet_id)}&theme=dark&dnt=true&hideThread=false`
      : escHtml(p.embed_url);
    const style = p.embed_height ? ` style="aspect-ratio:auto;height:${+p.embed_height}px"` : '';
    const frame = `<iframe src="${src}"${style} allowfullscreen loading="lazy" scrolling="no"></iframe>`;
    const content = p.embed_aspect_ratio && !p.embed_height
      ? `<div class="responsive-embed" style="aspect-ratio:${escHtml(p.embed_aspect_ratio)}">${frame}</div>`
      : frame;
    html = `<div class="${vc}">${content}</div>`;
  } else if (p.gif_url) {
    html = p.gif_is_video
      ? `<div class="${vc}"><video src="${escHtml(p.gif_url)}" controls autoplay loop muted playsinline></video></div>`
      : `<div class="${ic}"><img src="${escHtml(p.gif_url)}" loading="lazy" alt="" data-onerror="no-media"></div>`;
  } else if (p.gallery?.length > (full ? 0 : 1)) {
    html = renderGallery(p.gallery, full);
  } else {
    const imgSrc = p.gallery?.length ? (full ? p.gallery[0].url : galleryThumb(p.gallery[0])) : (!p.is_self ? (full ? p.preview_img : (p.thumb_url ?? p.preview_img)) : null);
    if (imgSrc) {
      const fullAttr = p.gallery?.length && !full ? ` data-full="${escHtml(p.gallery[0].url)}"` : '';
      html = `<div class="${ic}"><img src="${escHtml(imgSrc)}"${fullAttr} loading="lazy" alt="" data-onerror="no-media"></div>`;
    } else if (!p.is_self && p.url && /^https?:\/\//.test(p.url)) {
      html = `<div class="og-placeholder ${ic}" data-og-url="${escHtml(p.url)}"></div>`;
    }
  }
  if (!html) return '';
  if (p.is_spoiler) html = veilWrap('spoiler', html);
  if (p.over_18)   html = veilWrap('nsfw', html);
  return html;
}

export const mediaHtmlCard = p => mediaHtml(p, false);
export const mediaHtmlFull = p => mediaHtml(p, true);
