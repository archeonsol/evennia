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
  /** Incremented on each load/seek; stale onStateChange handlers are ignored. */
  private playGen = 0;
  /** Target sync offset for the active playGen (seconds). */
  private syncOffset = 0;
  /** playGen value for which we already enforced a post-load seek. */
  private syncSeekDoneGen = 0;
  private activeVideoId: string | null = null;
  private apiErrorHandler: ((msg: string) => void) | null = null;
  apiLoadFailed = false;

  private static readonly API_LOAD_TIMEOUT_MS = 15_000;

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

  /** Same track — seek/sync; skip re-fade when already audible. */
  syncSameTrack(videoId: string, offsetSeconds: number, loop: boolean): void {
    this.roomLoop = loop;
    const offset = Math.max(0, parseInt(String(offsetSeconds), 10) || 0);
    this.activeVideoId = videoId;
    if (!this.player || typeof this.player.seekTo !== "function") {
      this.playSequence(videoId, offset, loop);
      return;
    }
    try {
      if (this.isAudible()) {
        this.syncOffset = offset;
        this.player.seekTo(offset, true);
        this.player.playVideo?.();
        return;
      }
    } catch {
      /* fall through to full play */
    }
    this.playSequence(videoId, offset, loop);
  }

  playSequence(videoId: string, offsetSeconds: number, loop: boolean): void {
    this.roomLoop = loop;
    // Legacy custom-client.js: parseInt(offsetSeconds, 10)
    const offset = Math.max(0, parseInt(String(offsetSeconds), 10) || 0);
    this.activeVideoId = videoId;

    if (!this.player || typeof this.player.loadVideoById !== "function") {
      this.pendingPlay = { id: videoId, offset, loop };
      if (document.getElementById(this.hostId)) {
        void this.init(this.hostId).catch(() => {});
      }
      return;
    }

    this.cancelFade();
    ++this.playGen;
    this.syncOffset = offset;
    this.syncSeekDoneGen = 0;

    // Legacy playYtSequence: cancel fade → loadVideoById → volume 0 → fadeIn immediately.
    this.loadFresh(videoId, offset);
    try {
      if (this.player.isMuted?.()) this.player.unMute();
      this.player.setVolume(0);
    } catch {
      /* ignore */
    }
    this.fadeIn(YT_FADE_MS);
  }

  private loadFresh(videoId: string, offset: number): void {
    if (!this.player?.loadVideoById) return;
    const start = Math.max(0, parseInt(String(offset), 10) || 0);
    // YT often ignores startSeconds when reloading the same cued video — stop first.
    try {
      const cur = this.player.getVideoData?.()?.video_id;
      if (cur === videoId && this.player.stopVideo) {
        this.player.stopVideo();
      }
    } catch {
      /* ignore */
    }
    this.player.loadVideoById({ videoId, startSeconds: start });
  }

  /** If loadVideoById did not land at syncOffset, seek once when playback starts. */
  private enforceSyncSeek(state: number): void {
    if (
      this.syncSeekDoneGen === this.playGen ||
      this.syncOffset <= 0 ||
      !this.player?.seekTo ||
      !this.player.getCurrentTime
    ) {
      return;
    }
    const PS = window.YT?.PlayerState;
    if (PS && state !== PS.PLAYING && state !== PS.BUFFERING) return;

    try {
      const now = this.player.getCurrentTime();
      if (Math.abs(now - this.syncOffset) > 1.5) {
        this.player.seekTo(this.syncOffset, true);
      }
      this.syncSeekDoneGen = this.playGen;
    } catch {
      /* ignore */
    }
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
    this.pendingPlay = null;
    this.playGen += 1;
    this.activeVideoId = null;
    this.syncOffset = 0;
    this.syncSeekDoneGen = 0;
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
        this.stopNow();
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

    this.enforceSyncSeek(state);

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
