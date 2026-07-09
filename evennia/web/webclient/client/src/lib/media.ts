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

/** Extract a YouTube video id from a URL or bare 11-char id (room DJ / @music). */
export function youtubeId(raw: string): string | null {
  const s = (raw || "").trim();
  if (!s) return null;
  const m = s.match(
    /(?:youtube(?:-nocookie)?\.com\/(?:watch\?v=|embed\/|v\/)|youtu\.be\/|music\.youtube\.com\/watch\?v=)([\w-]{6,})/i,
  );
  if (m) return m[1];
  if (/^[A-Za-z0-9_-]{11}$/.test(s)) return s;
  return null;
}

export function youtubeEmbedUrl(
  id: string,
  opts: { start?: number; loop?: boolean; autoplay?: boolean } = {},
): string {
  const start = Math.max(0, Math.floor(opts.start ?? 0));
  const loop = !!opts.loop;
  const params = new URLSearchParams({
    enablejsapi: "1",
    autoplay: opts.autoplay === false ? "0" : "1",
    playsinline: "1",
  });
  if (start > 0) params.set("start", String(start));
  if (loop) {
    params.set("loop", "1");
    // YouTube requires playlist= for single-video loop.
    params.set("playlist", id);
  }
  try {
    params.set("origin", location.origin);
  } catch {
    /* ignore (non-browser) */
  }
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?${params}`;
}

export function mediaHtml(type: string, rawUrl: string): string {
  const yt = youtubeId(rawUrl);
  if (yt || type === "youtube") {
    const id = yt ?? youtubeId(String(rawUrl).trim());
    if (!id) return "";
    const embed = escapeAttr(youtubeEmbedUrl(id));
    return (
      `<span class="media media-yt"><iframe src="${embed}" title="YouTube player" ` +
      `frameborder="0" allow="autoplay; encrypted-media; picture-in-picture" ` +
      `allowfullscreen loading="lazy"></iframe></span>`
    );
  }

  const url = safeUrl(rawUrl);
  if (!url) return "";
  const src = escapeAttr(url);
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
