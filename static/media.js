// Public entry for media: markup builders and the init calls views run after
// rendering. The pieces live in media-{html,gallery,video,loaders,download}.js.
import { initVideos, initGifVideos, initGifImages, setupHls, syncAudio } from './media-video.js';
import { observeRedgifsPrefetch, initImgurAlbums, initOgImages, initOgDescriptions } from './media-loaders.js';

export { initVideos, initGifVideos, initGifImages, setupHls, syncAudio };
export { observeRedgifsPrefetch, initImgurAlbums, initOgImages, initOgDescriptions };
export { galleryThumb, galleryMini, renderGallery } from './media-gallery.js';
export { mediaHtml, mediaHtmlCard, mediaHtmlFull, linksOutMedia, mediaLinkHref, mediaLinkDomain } from './media-html.js';

export function initMedia(container) {
  initVideos(container);
  observeRedgifsPrefetch(container);
  initImgurAlbums(container);
  initOgImages(container);
  initOgDescriptions(container);
}

document.addEventListener('click', e => {
  const btn = e.target.closest('.devvit-try-btn');
  if (!btn) return;
  const card = btn.closest('.devvit-card');
  if (!card) return;
  const permalink = card.dataset.permalink;
  if (!permalink) return;
  btn.disabled = true;
  btn.textContent = 'Loading…';
  fetch(`/api/devvit?url=${encodeURIComponent(permalink)}`, { cache: 'no-store' })
    .then(r => r.json())
    .then(d => {
      if (!d.embedded || !d.url) {
        btn.textContent = 'Not available';
        return;
      }
      // Without a parent Reddit page doing the postMessage handshake, the
      // Devvit SDK falls back to reading its render context (post id, poll
      // state, signed auth token) from the URL hash — forward what the
      // server already extracted so the app renders instead of erroring.
      const url = d.bridge && Object.keys(d.bridge).length
        ? `${d.url}#${encodeURIComponent(JSON.stringify(d.bridge))}`
        : d.url;
      // Devvit webviews send frame-ancestors allowing only reddit.com and
      // localhost/127.0.0.1 (dev/loopback testing) — anywhere else, inline
      // iframing is blocked by the browser, so fall back to a new tab.
      const isLoopback = ['localhost', '127.0.0.1'].includes(location.hostname);
      if (!isLoopback) {
        window.open(url, '_blank', 'noopener');
        btn.disabled = false;
        btn.textContent = 'Try App';
        return;
      }
      const height = Math.max(300, Math.min(d.height || 512, 800));
      const iframe = document.createElement('iframe');
      iframe.src = url;
      iframe.className = 'devvit-iframe';
      iframe.style.height = `${height}px`;
      iframe.allow = 'autoplay; clipboard-write';
      iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups');
      card.replaceWith(iframe);
    })
    .catch(() => { btn.disabled = false; btn.textContent = 'Failed — retry'; });
});

// Tweet embeds report their rendered height to the parent: Twitter's own iframe
// (twttr.embed JSON-RPC) and Reddit's wrapper page ('tweet-measured').
window.addEventListener('message', e => {
  let d = e.data, h = 0;
  if (typeof d === 'string') { try { d = JSON.parse(d); } catch { return; } }
  if (!d || typeof d !== 'object') return;
  if (d.action === 'tweet-measured') h = d.height;
  else if (d['twttr.embed']?.method === 'twttr.private.resize') h = d['twttr.embed'].params?.[0]?.height;
  if (!(h > 0)) return;
  for (const f of document.querySelectorAll('iframe[src*="redditmedia.com/mediaembed/"], iframe[src*="platform.twitter.com/embed/"]')) {
    if (f.contentWindow === e.source) { f.style.aspectRatio = 'auto'; f.style.height = `${Math.ceil(h)}px`; }
  }
});
