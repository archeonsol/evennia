// Persistent media store: images collect into a gallery; audio/video/YouTube
// become the "now playing" so music doesn't scroll away in the log. Fed from the
// same server events as the inline log media.

import { mediaHtml, parseYoutubeStart, youtubeId } from "./media";
import { session } from "./session.svelte";
import { dock } from "./dock.svelte";
import { ytBgm, YT_FADE_MS } from "./youtube-bgm.svelte";

export interface MediaItem {
  type: string;
  url: string;
  html: string;
}

const VOL_KEY = "underspire.media.volume";
const LEGACY_VOL_KEY = "mud_terminal_settings";

class MediaStore {
  images = $state<MediaItem[]>([]);
  nowPlaying = $state<MediaItem | null>(null);
  volume = $state<number>(40); // 0..100 — legacy default 0.4
  ytStart = $state(0);
  ytLoop = $state(false);
  audioLoop = $state(false);

  private html5Track: HTMLAudioElement | null = null;
  private html5FadeTimer: ReturnType<typeof setInterval> | null = null;
  private ytPlayPending: { id: string; start: number; loop: boolean } | null = null;
  private queuedYoutubePlay: { id: string; start: number; loop: boolean } | null = null;
  private crossfadeOutActive = false;
  private stopGeneration = 0;
  ytApiWarned = false;

  constructor() {
    this.loadVolume();
    ytBgm.setVolumeGetter(() => this.volume);
    ytBgm.setApiErrorHandler((msg) => {
      if (this.ytApiWarned) return;
      this.ytApiWarned = true;
      session.append(`<span class="media-note warn">♪ ${msg}</span>`, "media");
    });
  }

  private loadVolume(): void {
    try {
      const v = localStorage.getItem(VOL_KEY);
      if (v != null) {
        this.volume = Math.max(0, Math.min(100, +v));
        return;
      }
      const legacy = localStorage.getItem(LEGACY_VOL_KEY);
      if (legacy) {
        const settings = JSON.parse(legacy) as { musicVolume?: number };
        if (settings.musicVolume != null) {
          this.volume = Math.max(0, Math.min(100, Math.round(settings.musicVolume * 100)));
        }
      }
    } catch {
      /* ignore */
    }
  }

  get isFadeActive(): boolean {
    return ytBgm.isFadeActive() || this.html5FadeTimer != null;
  }

  setVolume(v: number): void {
    this.volume = Math.max(0, Math.min(100, Math.round(v)));
    try {
      localStorage.setItem(VOL_KEY, String(this.volume));
    } catch {
      /* ignore */
    }
    if (!this.isFadeActive) {
      ytBgm.applyVolume();
      if (this.html5Track) this.html5Track.volume = this.volume / 100;
    }
  }

  private noteNowPlaying(url: string, opts: { openDock?: boolean } = {}): void {
    const safe = url.replace(/"/g, "&quot;");
    session.append(
      `<span class="media-note">♪ media: <a href="${safe}" target="_blank" rel="noopener">${safe}</a></span>`,
      "media",
    );
    if (opts.openDock) dock.openView("media");
  }

  private currentYoutubeId(): string | null {
    if (this.nowPlaying?.type !== "youtube") return null;
    return youtubeId(this.nowPlaying.url);
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
      session.append(html, "media");
    } else if (type === "video") {
      this.stopYoutubeNow();
      this.stopHtml5Now();
      this.ytStart = 0;
      this.ytLoop = false;
      this.audioLoop = !!opts.loop;
      this.nowPlaying = item;
      this.noteNowPlaying(url, { openDock: true });
    } else {
      this.playHtml5(url, { loop: !!opts.loop });
    }
  }

  /** Coalesce rapid `youtube` + `play_yt` pairs; last write wins per tick. */
  playYoutube(raw: string, startSeconds = 0, loop = false): void {
    const id = youtubeId(raw);
    if (!id) return;
    let start = Math.max(0, Number(startSeconds) || 0);
    if (!startSeconds) {
      const fromUrl = parseYoutubeStart(raw);
      if (fromUrl != null) start = fromUrl;
    }
    this.ytPlayPending = { id, start, loop };
    queueMicrotask(() => this.flushYoutubePlay());
  }

  private flushYoutubePlay(): void {
    const pending = this.ytPlayPending;
    this.ytPlayPending = null;
    if (!pending) return;

    const currentId = this.currentYoutubeId() ?? ytBgm.getActiveVideoId();
    if (currentId === pending.id && (ytBgm.isPlaying() || this.nowPlaying?.type === "youtube")) {
      this.applyQuietYoutubeSync(pending);
      return;
    }

    if (ytBgm.isFadeActive()) {
      this.queuedYoutubePlay = pending;
      return;
    }

    const playingOther =
      ytBgm.isPlaying() && currentId != null && currentId !== pending.id;
    if (playingOther) {
      this.queuedYoutubePlay = pending;
      if (!this.crossfadeOutActive) {
        this.crossfadeOutActive = true;
        ytBgm.fadeOut(YT_FADE_MS, () => {
          this.crossfadeOutActive = false;
          this.clearPlayback();
          this.drainYoutubeQueue();
        });
      }
      return;
    }

    this.startYoutubePlay(pending);
  }

  /** Same track, new offset — seek only; no log line or dock. */
  private applyQuietYoutubeSync(pending: { id: string; start: number; loop: boolean }): void {
    this.ytStart = pending.start;
    this.ytLoop = pending.loop;
    ytBgm.setRoomLoop(pending.loop);
    ytBgm.playSequence(pending.id, pending.start, pending.loop);
  }

  private drainYoutubeQueue(): void {
    const next = this.queuedYoutubePlay;
    this.queuedYoutubePlay = null;
    if (next) this.startYoutubePlay(next);
  }

  private startYoutubePlay(pending: { id: string; start: number; loop: boolean }): void {
    this.stopGeneration += 1;
    ytBgm.cancelFade();

    this.stopHtml5Now();
    this.ytStart = pending.start;
    this.ytLoop = pending.loop;
    this.audioLoop = false;
    const url = `https://www.youtube.com/watch?v=${pending.id}`;
    const html = mediaHtml("youtube", url);
    if (!html) return;
    this.nowPlaying = { type: "youtube", url, html };
    this.noteNowPlaying(url);
    ytBgm.setRoomLoop(pending.loop);
    ytBgm.playSequence(pending.id, pending.start, pending.loop);
  }

  setYtLoop(enabled: boolean): void {
    this.ytLoop = enabled;
    ytBgm.setRoomLoop(enabled);
  }

  /** Legacy `play_music` — HTML5 loop, stops YouTube immediately. */
  playMusic(url: string): void {
    this.stopYoutubeNow();
    this.playHtml5(url, { loop: true, fadeIn: 0 });
  }

  /** `PLAY_AUDIO` OOB with optional fade-in. */
  playAudio(
    url: string,
    opts: { loop?: boolean; volume?: number; fadeIn?: number } = {},
  ): void {
    this.stopYoutubeNow();
    const vol = opts.volume != null ? Math.round(opts.volume * 100) : this.volume;
    this.playHtml5(url, {
      loop: opts.loop ?? true,
      volume: vol,
      fadeIn: opts.fadeIn ?? 2,
    });
  }

  /** `SET_AUDIO_VOLUME` OOB — fade slider level (also drives YouTube when idle). */
  setAudioVolume(volume0to1: number, fadeSeconds = 1): void {
    const target = Math.max(0, Math.min(100, Math.round(volume0to1 * 100)));
    if (fadeSeconds <= 0 || !this.html5Track) {
      this.setVolume(target);
      return;
    }
    this.fadeVolumeTo(target, fadeSeconds * 1000);
  }

  private playHtml5(
    url: string,
    opts: { loop?: boolean; volume?: number; fadeIn?: number } = {},
  ): void {
    this.stopHtml5Now();
    const targetVol = (opts.volume ?? this.volume) / 100;
    const fadeMs = Math.max(0, (opts.fadeIn ?? 0) * 1000);
    const track = new Audio(url);
    track.loop = opts.loop ?? true;
    track.volume = fadeMs > 0 ? 0 : targetVol;
    this.html5Track = track;
    this.audioLoop = track.loop;
    this.ytStart = 0;
    this.ytLoop = false;
    const html = mediaHtml("audio", url);
    if (html) {
      this.nowPlaying = { type: "audio", url, html };
      this.noteNowPlaying(url, { openDock: true });
    }
    track.play().catch(() => {
      const resume = () => {
        track.play().catch(() => {});
        document.removeEventListener("click", resume);
        document.removeEventListener("keydown", resume);
      };
      document.addEventListener("click", resume, { once: true });
      document.addEventListener("keydown", resume, { once: true });
    });
    if (fadeMs > 0) this.fadeHtml5Volume(0, targetVol, fadeMs);
  }

  /** Room leave / `stop_music` — fade out then clear UI state. */
  stop(fadeMs?: number): void {
    const ms = fadeMs ?? YT_FADE_MS;
    const gen = ++this.stopGeneration;
    let pending = 0;
    const done = (): void => {
      if (gen !== this.stopGeneration) return;
      pending -= 1;
      if (pending <= 0) {
        this.clearPlayback();
        this.drainYoutubeQueue();
      }
    };

    const fadeYoutube =
      this.nowPlaying?.type === "youtube" || ytBgm.isPlaying();
    if (fadeYoutube) {
      pending += 1;
      ytBgm.fadeOut(ms, done);
    }
    if (this.html5Track) {
      pending += 1;
      this.fadeOutHtml5(ms, done);
    }
    if (pending === 0) this.clearPlayback();
  }

  /** `@musicstop` / `stop_music_now` — immediate cut. */
  stopNow(): void {
    this.stopGeneration += 1;
    this.queuedYoutubePlay = null;
    this.crossfadeOutActive = false;
    this.stopYoutubeNow();
    this.stopHtml5Now();
    this.clearPlayback();
  }

  private stopYoutubeNow(): void {
    ytBgm.stopNow();
  }

  private stopHtml5Now(): void {
    this.cancelHtml5Fade();
    if (this.html5Track) {
      this.html5Track.pause();
      this.html5Track.currentTime = 0;
      this.html5Track = null;
    }
  }

  private clearPlayback(): void {
    this.nowPlaying = null;
    this.ytStart = 0;
    this.ytLoop = false;
    this.audioLoop = false;
  }

  private cancelHtml5Fade(): void {
    if (this.html5FadeTimer) {
      clearInterval(this.html5FadeTimer);
      this.html5FadeTimer = null;
    }
  }

  private fadeOutHtml5(durationMs: number, onDone?: () => void): void {
    this.cancelHtml5Fade();
    const track = this.html5Track;
    if (!track) {
      onDone?.();
      return;
    }
    const startVol = track.volume;
    const t0 = Date.now();
    this.html5FadeTimer = setInterval(() => {
      if (!this.html5Track) {
        this.cancelHtml5Fade();
        onDone?.();
        return;
      }
      const t = (Date.now() - t0) / durationMs;
      if (t >= 1) {
        this.cancelHtml5Fade();
        track.pause();
        track.currentTime = 0;
        this.html5Track = null;
        onDone?.();
        return;
      }
      track.volume = Math.max(0, startVol * (1 - t));
    }, 50);
  }

  private fadeHtml5Volume(from: number, to: number, durationMs: number): void {
    this.cancelHtml5Fade();
    const track = this.html5Track;
    if (!track || durationMs <= 0) return;
    const t0 = Date.now();
    this.html5FadeTimer = setInterval(() => {
      if (!this.html5Track) {
        this.cancelHtml5Fade();
        return;
      }
      const t = (Date.now() - t0) / durationMs;
      if (t >= 1) {
        this.cancelHtml5Fade();
        track.volume = to;
        return;
      }
      track.volume = from + (to - from) * t;
    }, 50);
  }

  private fadeVolumeTo(targetPct: number, durationMs: number): void {
    const fromPct = this.volume;
    if (durationMs <= 0 || fromPct === targetPct) {
      this.setVolume(targetPct);
      return;
    }
    const t0 = Date.now();
    const timer = setInterval(() => {
      const t = (Date.now() - t0) / durationMs;
      if (t >= 1) {
        clearInterval(timer);
        this.setVolume(targetPct);
        return;
      }
      const next = Math.round(fromPct + (targetPct - fromPct) * t);
      this.volume = next;
      if (!this.isFadeActive) {
        ytBgm.applyVolume();
        if (this.html5Track) this.html5Track.volume = next / 100;
      }
    }, 50);
  }

  /** Short label for HUD now-playing chip. */
  nowPlayingLabel(): string {
    const np = this.nowPlaying;
    if (!np) return "";
    if (np.type === "youtube") {
      const id = youtubeId(np.url);
      return id ? `♪ ${id.slice(0, 11)}` : "♪ yt";
    }
    if (np.type === "audio") return "♪ audio";
    if (np.type === "video") return "▶ video";
    return "";
  }

  openNowPlaying(): void {
    if (this.nowPlaying) dock.openView("media");
  }

  clearImages(): void {
    this.images = [];
  }
}

export const media = new MediaStore();
