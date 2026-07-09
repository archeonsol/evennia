// Hidden YouTube BGM player — parity with mootest custom-client.js (YT IFrame API,
// 2.8s fade in/out, manual loop on ENDED). The #yt-player DOM stays mounted always.

export const YT_FADE_MS = 2800;

interface YtPlayer {
  setVolume(n: number): void;
  getVolume(): number;
  getPlayerState(): number;
  loadVideoById(options: { videoId: string; startSeconds?: number }): void;
  stopVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  playVideo(): void;
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
            onReady?: () => void;
            onStateChange?: (ev: { data: number }) => void;
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

  setVolumeGetter(fn: () => number): void {
    this.volumeFn = fn;
  }

  isFadeActive(): boolean {
    return this.fadeActive;
  }

  /** True when the iframe player has active (or paused) media loaded. */
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

  /** Load iframe API and construct YT.Player on `hostId`. */
  init(hostId = "yt-player"): Promise<void> {
    this.hostId = hostId;
    if (!this.readyPromise) {
      this.readyPromise = this.loadApi().then(() => this.createPlayer());
    }
    return this.readyPromise;
  }

  private loadApi(): Promise<void> {
    if (window.YT?.Player) return Promise.resolve();
    return new Promise((resolve) => {
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        prev?.();
        resolve();
      };
      if (!document.querySelector('script[src*="youtube.com/iframe_api"]')) {
        const tag = document.createElement("script");
        tag.src = "https://www.youtube.com/iframe_api";
        document.head.appendChild(tag);
      }
    });
  }

  private createPlayer(): Promise<void> {
    return new Promise((resolve) => {
      if (!window.YT?.Player) {
        resolve();
        return;
      }
      const p = new window.YT.Player(this.hostId, {
        height: "1",
        width: "1",
        playerVars: { autoplay: 1, controls: 0, modestbranding: 1, playsinline: 1 },
        events: {
          onReady: () => {
            this.player = p as unknown as YtPlayer;
            if (this.pendingPlay) {
              const pending = this.pendingPlay;
              this.pendingPlay = null;
              this.roomLoop = pending.loop;
              this.playSequence(pending.id, pending.offset, pending.loop);
            } else {
              this.applyVolume();
            }
            resolve();
          },
          onStateChange: (ev) => this.onStateChange(ev),
        },
      });
      void p;
    });
  }

  setRoomLoop(enabled: boolean): void {
    this.roomLoop = enabled;
  }

  playSequence(videoId: string, offsetSeconds: number, loop: boolean): void {
    this.roomLoop = loop;
    const offset = Math.max(0, Math.floor(offsetSeconds));
    if (!this.player || typeof this.player.loadVideoById !== "function") {
      this.pendingPlay = { id: videoId, offset, loop };
      if (document.getElementById(this.hostId)) void this.init(this.hostId);
      return;
    }
    this.cancelFade();
    this.player.loadVideoById({ videoId, startSeconds: offset });
    try {
      this.player.setVolume(0);
    } catch {
      /* ignore */
    }
    this.fadeIn(YT_FADE_MS);
  }

  applyVolume(): void {
    if (this.fadeActive || !this.player?.setVolume) return;
    try {
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

  private onStateChange(ev: { data: number }): void {
    if (
      window.YT?.PlayerState?.ENDED === ev.data &&
      this.roomLoop &&
      this.player?.seekTo
    ) {
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
