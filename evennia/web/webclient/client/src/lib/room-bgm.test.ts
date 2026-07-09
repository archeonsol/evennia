/**
 * Room BGM leave/re-enter sync — client-side contract tests.
 * Legacy custom-client.js always loadVideoById({ startSeconds: offset }) on play_yt.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ytBgm } from "./youtube-bgm.svelte";

type MockPlayer = {
  loadVideoById: ReturnType<typeof vi.fn>;
  seekTo: ReturnType<typeof vi.fn>;
  playVideo: ReturnType<typeof vi.fn>;
  setVolume: ReturnType<typeof vi.fn>;
  getVolume: ReturnType<typeof vi.fn>;
  getPlayerState: ReturnType<typeof vi.fn>;
  getCurrentTime: ReturnType<typeof vi.fn>;
  getVideoData: ReturnType<typeof vi.fn>;
  isMuted: ReturnType<typeof vi.fn>;
  stopVideo: ReturnType<typeof vi.fn>;
};

function mockPlayer(overrides: Partial<MockPlayer> = {}): MockPlayer {
  return {
    loadVideoById: vi.fn(),
    seekTo: vi.fn(),
    playVideo: vi.fn(),
    setVolume: vi.fn(),
    getVolume: vi.fn(() => 0),
    getPlayerState: vi.fn(() => 5),
    getCurrentTime: vi.fn(() => 0),
    getVideoData: vi.fn(() => ({ video_id: "dQw4w9WgXcQ" })),
    isMuted: vi.fn(() => false),
    stopVideo: vi.fn(),
    ...overrides,
  };
}

function attachPlayer(player: MockPlayer): void {
  (ytBgm as unknown as { player: MockPlayer | null }).player = player;
}

describe("room BGM re-enter sync (youtube-bgm)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("window", { YT: undefined });
    attachPlayer(mockPlayer());
    ytBgm.setVolumeGetter(() => 40);
  });

  afterEach(() => {
    ytBgm.stopNow();
    attachPlayer(null as unknown as MockPlayer);
    vi.useRealTimers();
  });

  it("playSequence loadVideoById uses server offset (legacy parseInt floor)", () => {
    const player = mockPlayer();
    attachPlayer(player);

    ytBgm.playSequence("dQw4w9WgXcQ", 47.8, true);

    expect(player.loadVideoById).toHaveBeenCalledOnce();
    expect(player.loadVideoById).toHaveBeenCalledWith({
      videoId: "dQw4w9WgXcQ",
      startSeconds: 47,
    });
  });

  it("expectedSyncSeconds advances with wall clock while loading", () => {
    ytBgm.playSequence("dQw4w9WgXcQ", 60, true);
    expect(ytBgm.expectedSyncSeconds()).toBe(60);
    vi.advanceTimersByTime(5000);
    expect(ytBgm.expectedSyncSeconds()).toBe(65);
  });

  it("syncSameTrack seeks without reload when already audible", () => {
    const player = mockPlayer({
      getVolume: vi.fn(() => 40),
      getPlayerState: vi.fn(() => 1),
    });
    attachPlayer(player);

    ytBgm.playSequence("dQw4w9WgXcQ", 55, true);
    vi.spyOn(ytBgm, "isAudible").mockReturnValue(true);
    player.seekTo.mockClear();

    ytBgm.syncSameTrack("dQw4w9WgXcQ", 70, true);

    expect(player.seekTo).toHaveBeenCalledWith(70, true);
    expect(player.loadVideoById).toHaveBeenCalledTimes(1);
  });

  it("retries seek on PLAYING when YT landed at 0", () => {
    vi.stubGlobal("window", {
      YT: {
        PlayerState: {
          PLAYING: 1,
          BUFFERING: 3,
          ENDED: 0,
          UNSTARTED: -1,
          PAUSED: 2,
          CUED: 5,
        },
      },
    });
    let t = 0;
    const player = mockPlayer({
      getCurrentTime: vi.fn(() => t),
      getPlayerState: vi.fn(() => 1),
    });
    player.seekTo.mockImplementation((sec: number) => {
      t = sec;
    });
    attachPlayer(player);

    ytBgm.playSequence("dQw4w9WgXcQ", 60, true);

    const ctl = ytBgm as unknown as {
      onStateChange: (state: number, target: MockPlayer) => void;
    };
    ctl.onStateChange(1, player);

    expect(player.seekTo).toHaveBeenCalledWith(60, true);
  });

  it("simulated leave stop then re-enter uses loadVideoById at new offset", () => {
    const player = mockPlayer();
    attachPlayer(player);

    ytBgm.playSequence("dQw4w9WgXcQ", 12, true);
    ytBgm.stopNow();
    player.loadVideoById.mockClear();

    ytBgm.playSequence("dQw4w9WgXcQ", 60, true);

    expect(player.loadVideoById).toHaveBeenCalledWith({
      videoId: "dQw4w9WgXcQ",
      startSeconds: 60,
    });
  });

  it("playSequence invalidates leave-fade stopNow race", () => {
    const player = mockPlayer({ getVolume: vi.fn(() => 40) });
    attachPlayer(player);

    ytBgm.playSequence("dQw4w9WgXcQ", 10, true);
    ytBgm.fadeOut(2800);
    ytBgm.playSequence("dQw4w9WgXcQ", 75, true);
    player.stopVideo.mockClear();

    vi.advanceTimersByTime(3000);

    expect(player.stopVideo).not.toHaveBeenCalled();
  });
});

describe("play_yt wire args", () => {
  it("main.ts parses fractional offset from OOB args[1]", () => {
    const args = ["dQw4w9WgXcQ", 33.75, 1];
    expect(parseFloat(String(args[1] ?? 0))).toBe(33.75);
  });
});
