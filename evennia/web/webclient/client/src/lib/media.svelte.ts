// Persistent media store: images collect into a gallery; audio/video/YouTube
// become the "now playing" so music doesn't scroll away in the log. Fed from the
// same server events as the inline log media.

import { mediaHtml } from "./media";
import { session } from "./session.svelte";

export interface MediaItem {
  type: string;
  url: string;
  html: string;
}

const VOL_KEY = "underspire.media.volume";

class MediaStore {
  images = $state<MediaItem[]>([]);
  nowPlaying = $state<MediaItem | null>(null);
  volume = $state<number>(80); // 0..100

  constructor() {
    try {
      const v = localStorage.getItem(VOL_KEY);
      if (v != null) this.volume = Math.max(0, Math.min(100, +v));
    } catch {
      /* ignore */
    }
  }

  setVolume(v: number): void {
    this.volume = Math.max(0, Math.min(100, Math.round(v)));
    try {
      localStorage.setItem(VOL_KEY, String(this.volume));
    } catch {
      /* ignore */
    }
  }

  add(type: string, url: string): void {
    const html = mediaHtml(type, url);
    if (!html) return;
    const item = { type, url, html };
    if (type === "image") {
      this.images = [...this.images, item].slice(-60);
      session.append(html, "media"); // images still show inline
    } else {
      // Audio / video / YouTube: park it in the panel (persistent player) and
      // leave a compact marker in the log instead of a scrolling player.
      this.nowPlaying = item;
      const safe = url.replace(/"/g, "&quot;");
      session.append(
        `<span class="media-note">♪ media: <a href="${safe}" target="_blank" rel="noopener">${safe}</a> - see the Media panel</span>`,
        "media",
      );
    }
  }

  stop(): void {
    this.nowPlaying = null;
  }
  clearImages(): void {
    this.images = [];
  }
}

export const media = new MediaStore();
