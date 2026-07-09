// Persistent media store: images collect into a gallery; audio/video/YouTube
// become the "now playing" so music doesn't scroll away in the log. Fed from the
// same server events as the inline log media.

import { mediaHtml, youtubeId } from "./media";
import { session } from "./session.svelte";
import { dock } from "./dock.svelte";

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
  ytStart = $state(0);
  ytLoop = $state(false);
  audioLoop = $state(false);

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

  private noteNowPlaying(url: string): void {
    const safe = url.replace(/"/g, "&quot;");
    session.append(
      `<span class="media-note">♪ media: <a href="${safe}" target="_blank" rel="noopener">${safe}</a></span>`,
      "media",
    );
    dock.openView("media");
  }

  add(type: string, url: string, opts: { loop?: boolean; start?: number } = {}): void {
    if (type === "youtube") {
      this.playYoutube(url, opts.start ?? 0, opts.loop ?? false);
      return;
    }
    const html = mediaHtml(type, url);
    if (!html) return;
    const item = { type, url, html };
    if (type === "image") {
      this.images = [...this.images, item].slice(-60);
      session.append(html, "media"); // images still show inline
    } else {
      this.audioLoop = !!opts.loop;
      this.ytStart = 0;
      this.ytLoop = false;
      this.nowPlaying = item;
      this.noteNowPlaying(url);
    }
  }

  /** Legacy room-DJ / @music path: bare id or URL, optional sync offset + loop. */
  playYoutube(raw: string, startSeconds = 0, loop = false): void {
    const id = youtubeId(raw);
    if (!id) return;
    const start = Math.max(0, Math.floor(startSeconds));
    const same =
      this.nowPlaying?.type === "youtube" &&
      youtubeId(this.nowPlaying.url) === id &&
      this.ytStart === start &&
      this.ytLoop === loop;
    if (same) return;
    this.ytStart = start;
    this.ytLoop = loop;
    this.audioLoop = false;
    const url = `https://www.youtube.com/watch?v=${id}`;
    const html = mediaHtml("youtube", url);
    if (!html) return;
    this.nowPlaying = { type: "youtube", url, html };
    this.noteNowPlaying(url);
  }

  setYtLoop(enabled: boolean): void {
    this.ytLoop = enabled;
  }

  stop(): void {
    this.nowPlaying = null;
    this.ytStart = 0;
    this.ytLoop = false;
    this.audioLoop = false;
  }
  clearImages(): void {
    this.images = [];
  }
}

export const media = new MediaStore();
