// Server-driven multimedia: target.msg(image=/audio=/video=/youtube=URL).
// Rendered as an inline line in the game log. YouTube URLs (from any of the
// audio/video/youtube channels) become an embedded player, so the game can push
// background music or clips.

import { session } from "./session.svelte";

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Only allow http(s) URLs; reject anything that could inject script. */
function safeUrl(url: string): string | null {
  const u = (url || "").trim();
  if (/^https?:\/\//i.test(u)) return u;
  if (/^\/\//.test(u)) return "https:" + u;
  if (/^\//.test(u)) return u; // same-origin absolute path (e.g. /static/...)
  return null;
}

export function youtubeId(url: string): string | null {
  const m = url.match(
    /(?:youtube(?:-nocookie)?\.com\/(?:watch\?v=|embed\/|v\/)|youtu\.be\/|music\.youtube\.com\/watch\?v=)([\w-]{6,})/i,
  );
  return m ? m[1] : null;
}

export function mediaHtml(type: string, rawUrl: string): string {
  const url = safeUrl(rawUrl);
  if (!url) return "";
  const src = escapeAttr(url);

  const yt = youtubeId(url);
  if (yt || type === "youtube") {
    const id = yt ?? "";
    if (!id) return "";
    const embed = `https://www.youtube-nocookie.com/embed/${escapeAttr(id)}`;
    return (
      `<span class="media media-yt"><iframe src="${embed}" title="YouTube player" ` +
      `frameborder="0" allow="autoplay; encrypted-media; picture-in-picture" ` +
      `allowfullscreen loading="lazy"></iframe></span>`
    );
  }
  if (type === "image") {
    return `<a href="${src}" target="_blank" rel="noopener"><img class="media media-img" src="${src}" alt="image" loading="lazy"></a>`;
  }
  if (type === "audio") {
    return `<audio class="media media-audio" controls preload="none" src="${src}"></audio>`;
  }
  if (type === "video") {
    return `<video class="media media-video" controls preload="none" src="${src}"></video>`;
  }
  // Unknown: fall back to a link.
  return `<a href="${src}" target="_blank" rel="noopener">${src}</a>`;
}

export function appendMedia(type: string, url: string): void {
  const html = mediaHtml(type, url);
  if (html) session.append(html, "media");
}
