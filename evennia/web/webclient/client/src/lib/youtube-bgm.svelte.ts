// Hidden YouTube BGM player — parity with mootest custom-client.js (YT IFrame API,
// 2.8s fade in/out, manual loop on ENDED). The #yt-player DOM stays mounted always.

export const YT_FADE_MS = 2800;

interface YtVideoData {
  video_id?: string;
}

interface YtPlayer {
  setVolume(n: number): void;
  getVolume(): number;
  getPlayerState(): number;
  getCurrentTime(): number;
  getVideoData(): YtVideoData;
  loadVideoById(options: { videoId: string; startSeconds?: number }): void;
  stopVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  playVideo(): void;
  mute(): void;
  unMute(): void;
  isMuted(): boolean;
}

declare global {
  namespace YT {
    const PlayerState: {
      UNSTARTED: number;
      ENDED: number;
      PLAYING: number;
      PAUSED: number;
      BUFFERING: number;
      CUED: number;
    };
    class Player {
      constructor(
        elementId: string,
        options: {
          height?: string | number;
          width?: string | number;
          playerVars?: Record<string, number | string>;
          events?: {
            onReady?: (ev: { target: YtPlayer }) => void;
            onStateChange?: (ev: { data: number; target: YtPlayer }) => void;
          };
        },
      );
    }
  }
  interface Window {
    YT?: typeof YT;
    onYouTubeIframeAPIReady?: () => void;
  }
}

class YoutubeBgmController {
  private player: YtPlayer | null = null;
  private fadeTimer: ReturnType<typeof setInterval> | null = null;
  fadeActive = false;
  private pendingPlay: { id: string; offset: number; loop: boolean } | null = null;
  private roomLoop = false;
  private volumeFn = (): number => 40;
  private readyPromise: Promise<void> | null = null;
  private hostId = "yt-player";
  /** Incremented on each load; stale retry handlers are ignored. */
  private playGen = 0;
  /** Server offset (seconds) when play_yt was applied. */
  private syncAnchorOffset = 0;
  /** Wall clock (ms) when play_yt was applied — room sync advances while loading. */
  private syncAnchorWallMs = 0;
  private syncRetryTimers: ReturnType<typeof setTimeout>[] = [];
  /** Bumped when a new play starts so a finishing leave-fade cannot stopVideo. */
  private fadeOutSession = 0;
  private activeVideoId: string | null = null;
  private apiErrorHandler: ((msg: string) => void) | null = null;
  apiLoadFailed = false;

  private static readonly API_LOAD_TIMEOUT_MS = 15_000;
  private static readonly SYNC_TOLERANCE_S = 1.5;

  setApiErrorHandler(fn: (msg: string) => void): void {
    this.apiErrorHandler = fn;
  }

  getActiveVideoId(): string | null {
    return this.activeVideoId;
  }

  setVolumeGetter(fn: () => number): void {
    this.volumeFn = fn;
  }

  isFadeActive(): boolean {
    return this.fadeActive;
  }

  isPlaying(): boolean {
    if (!this.player?.getPlayerState) return false;
    try {
      const st = this.player.getPlayerState();
      const PS = window.YT?.PlayerState;
      if (!PS) return st === 1 || st === 2 || st === 3;
      return st === PS.PLAYING || st === PS.BUFFERING || st === PS.PAUSED || st === PS.CUED;
    } catch {
      return false;
    }
  }

  /** Audible playback (not fading in/out, volume up). */
  isAudible(): boolean {
    if (this.fadeActive || !this.isPlaying()) return false;
    try {
      if (this.player?.isMuted?.()) return false;
      return (this.player?.getVolume?.() ?? 0) > 0;
    } catch {
      return false;
    }
  }

  /** Room elapsed seconds now (server offset + time since play_yt). */
  expectedSyncSeconds(): number {
    const elapsed = (Date.now() - this.syncAnchorWallMs) / 1000;
    return Math.max(0, this.syncAnchorOffset + elapsed);
  }

  init(hostId = "yt-player"): Promise<void> {
    this.hostId = hostId;
    if (!this.readyPromise) {
      this.readyPromise = this.loadApi().then(() => this.createPlayer());
    }
    return this.readyPromise;
  }

  private loadApi(): Promise<void> {
    if (window.YT?.Player) return Promise.resolve();
    return new Promise((resolve, reject) => {
      if (window.YT?.Player) {
        resolve();
        return;
      }
      let settled = false;
      const finish = (failed: boolean): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (failed) {
          this.apiLoadFailed = true;
          const msg =
            "YouTube player failed to load (adblock, CSP, or network). Room music may be silent.";
          console.warn("yt-bgm:", msg);
          this.apiErrorHandler?.(msg);
          reject(new Error(msg));
        } else {
          resolve();
        }
      };
      const timer = setTimeout(() => finish(true), YoutubeBgmController.API_LOAD_TIMEOUT_MS);
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        prev?.();
        finish(false);
      };
      if (!document.querySelector('script[src*="youtube.com/iframe_api"]')) {
        const tag = document.createElement("script");
        tag.src = "https://www.youtube.com/iframe_api";
        tag.onerror = () => finish(true);
        document.head.appendChild(tag);
      } else if (window.YT?.Player) {
        finish(false);
      }
    });
  }

  private createPlayer(): Promise<void> {
    return new Promise((resolve) => {
      if (!window.YT?.Player) {
        this.apiLoadFailed = true;
        resolve();
        return;
      }
      new window.YT.Player(this.hostId, {
        height: "1",
        width: "1",
        playerVars: { autoplay: 1, controls: 0, modestbranding: 1, playsinline: 1 },
        events: {
          onReady: (ev) => {
            this.player = ev.target;
            if (this.pendingPlay) {
              const pending = this.pendingPlay;
              this.pendingPlay = null;
              this.roomLoop = pending.loop;
              this.playSequence(pending.id, pending.offset, pending.loop);
            }
            resolve();
          },
          onStateChange: (ev) => this.onStateChange(ev.data, ev.target),
        },
      });
    });
  }

  setRoomLoop(enabled: boolean): void {
    this.roomLoop = enabled;
  }

  /** Same track — seek to live room offset when already audible. */
  syncSameTrack(videoId: string, offsetSeconds: number, loop: boolean): void {
    this.roomLoop = loop;
    this.setSyncAnchor(offsetSeconds);
    this.activeVideoId = videoId;
    if (!this.player || typeof this.player.seekTo !== "function") {
      this.playSequence(videoId, offsetSeconds, loop);
      return;
    }
    try {
      if (this.isAudible()) {
        this.player.seekTo(this.expectedSyncSeconds(), true);
        this.player.playVideo?.();
        return;
      }
    } catch {
      /* fall through to full play */
    }
    this.playSequence(videoId, offsetSeconds, loop);
  }

  playSequence(videoId: string, offsetSeconds: number, loop: boolean): void {
    this.roomLoop = loop;
    this.setSyncAnchor(offsetSeconds);
    this.activeVideoId = videoId;

    if (!this.player || typeof this.player.loadVideoById !== "function") {
      const offset = Math.max(0, parseFloat(String(offsetSeconds)) || 0);
      this.pendingPlay = { id: videoId, offset, loop };
      if (document.getElementById(this.hostId)) {
        void this.init(this.hostId).catch(() => {});
      }
      return;
    }

    // Invalidate any in-flight leave-fade completion that would call stopVideo.
    this.fadeOutSession += 1;
    this.cancelFade();
    const gen = ++this.playGen;

    // Legacy playYtSequence: cancel fade → loadVideoById → volume 0 → fadeIn immediately.
    this.loadFresh(videoId, this.expectedSyncSeconds());
    this.trySyncSeek(false);
    try {
      if (this.player.isMuted?.()) this.player.unMute();
      this.player.setVolume(0);
    } catch {
      /* ignore */
    }
    this.fadeIn(YT_FADE_MS);
    this.scheduleSyncRetries(gen);
  }

  private setSyncAnchor(offsetSeconds: number | string): void {
    this.syncAnchorOffset = Math.max(0, parseFloat(String(offsetSeconds)) || 0);
    this.syncAnchorWallMs = Date.now();
  }

  private loadFresh(videoId: string, offsetSeconds: number): void {
    if (!this.player?.loadVideoById) return;
    const start = Math.max(0, Math.floor(offsetSeconds));
    this.player.loadVideoById({ videoId, startSeconds: start });
  }

  /** Seek to live room offset; returns true when within tolerance. */
  private trySyncSeek(requirePlaying = true): boolean {
    if (!this.player?.seekTo || !this.player.getCurrentTime) return false;
    const target = this.expectedSyncSeconds();
    if (target <= 0) return true;

    if (requirePlaying) {
      const PS =
        typeof globalThis !== "undefined" &&
        "YT" in globalThis &&
        (globalThis as { YT?: typeof YT }).YT?.PlayerState;
      if (PS && this.player.getPlayerState() !== PS.PLAYING) return false;
    }

    try {
      const now = this.player.getCurrentTime();
      if (Math.abs(now - target) <= YoutubeBgmController.SYNC_TOLERANCE_S) return true;
      this.player.seekTo(target, true);
      const after = this.player.getCurrentTime();
      return Math.abs(after - target) <= YoutubeBgmController.SYNC_TOLERANCE_S;
    } catch {
      return false;
    }
  }

  private scheduleSyncRetries(gen: number): void {
    this.clearSyncRetries();
    for (const delay of [100, 300, 600, 1200, 2500, 5000]) {
      this.syncRetryTimers.push(
        setTimeout(() => {
          if (gen !== this.playGen) return;
          if (this.trySyncSeek()) this.clearSyncRetries();
        }, delay),
      );
    }
  }

  private clearSyncRetries(): void {
    for (const t of this.syncRetryTimers) clearTimeout(t);
    this.syncRetryTimers = [];
  }

  applyVolume(): void {
    if (this.fadeActive || !this.player?.setVolume) return;
    try {
      if (this.player.isMuted?.()) this.player.unMute();
      this.player.setVolume(Math.round(this.volumeFn()));
    } catch {
      /* ignore */
    }
  }

  cancelFade(): void {
    if (this.fadeTimer) {
      clearInterval(this.fadeTimer);
      this.fadeTimer = null;
    }
    this.fadeActive = false;
  }

  stopNow(): void {
    this.cancelFade();
    this.clearSyncRetries();
    this.pendingPlay = null;
    this.playGen += 1;
    this.activeVideoId = null;
    this.syncAnchorOffset = 0;
    this.syncAnchorWallMs = 0;
    if (this.player?.stopVideo) {
      try {
        this.player.stopVideo();
      } catch {
        /* ignore */
      }
    }
  }

  fadeOut(durationMs = YT_FADE_MS, onDone?: () => void): void {
    this.cancelFade();
    if (!this.player?.setVolume) {
      this.stopNow();
      onDone?.();
      return;
    }

    const session = this.fadeOutSession;

    try {
      if (this.player.isMuted?.()) this.player.unMute();
    } catch {
      /* ignore */
    }

    let startVol = 0;
    try {
      startVol = this.player.getVolume?.() ?? 0;
    } catch {
      /* ignore */
    }
    const sliderVol = Math.round(this.volumeFn());
    if (startVol < 1 && sliderVol > 0) startVol = sliderVol;
    if (startVol < 1) {
      this.stopNow();
      onDone?.();
      return;
    }

    const started = Date.now();
    this.fadeActive = true;
    this.fadeTimer = setInterval(() => {
      const t = (Date.now() - started) / durationMs;
      if (t >= 1) {
        this.cancelFade();
        if (session === this.fadeOutSession) {
          this.stopNow();
        }
        onDone?.();
        return;
      }
      try {
        this.player?.setVolume(Math.max(0, Math.round(startVol * (1 - t))));
      } catch {
        /* ignore */
      }
    }, 50);
  }

  fadeIn(durationMs = YT_FADE_MS): void {
    this.cancelFade();
    if (!this.player?.setVolume) return;
    try {
      if (this.player.isMuted?.()) this.player.unMute();
    } catch {
      /* ignore */
    }
    const started = Date.now();
    this.fadeActive = true;
    this.fadeTimer = setInterval(() => {
      const target = Math.round(this.volumeFn());
      const t = (Date.now() - started) / durationMs;
      if (t >= 1) {
        this.cancelFade();
        try {
          this.player?.setVolume(target);
        } catch {
          /* ignore */
        }
        return;
      }
      try {
        this.player?.setVolume(Math.round(target * t));
      } catch {
        /* ignore */
      }
    }, 50);
  }

  private onStateChange(state: number, target: YtPlayer): void {
    this.player = target;
    const PS = window.YT?.PlayerState;
    if (!PS) return;

    if (state === PS.PLAYING) {
      this.trySyncSeek(true);
    }

    if (state === PS.ENDED && this.roomLoop && this.player?.seekTo) {
      try {
        this.player.seekTo(0, true);
        this.player.playVideo();
      } catch {
        /* ignore */
      }
    }
  }
}

export const ytBgm = new YoutubeBgmController();
