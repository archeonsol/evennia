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
    attachPlayer(mockPlayer());
    ytBgm.setVolumeGetter(() => 40);
  });

  afterEach(() => {
    ytBgm.stopNow();
    attachPlayer(null as unknown as MockPlayer);
    vi.useRealTimers();
  });

  it("playSequence loadVideoById uses server offset after leave (same track still loaded)", () => {
    const player = mockPlayer();
    attachPlayer(player);

    ytBgm.playSequence("dQw4w9WgXcQ", 47.8, true);

    expect(player.loadVideoById).toHaveBeenCalledOnce();
    expect(player.loadVideoById).toHaveBeenCalledWith({
      videoId: "dQw4w9WgXcQ",
      startSeconds: 47,
    });
    expect(player.seekTo).not.toHaveBeenCalled();
  });

  it("syncSameTrack seeks when already audible (hello/resync without reload)", () => {
    const player = mockPlayer({
      getVolume: vi.fn(() => 40),
      getPlayerState: vi.fn(() => 1),
    });
    attachPlayer(player);
    vi.spyOn(ytBgm, "isAudible").mockReturnValue(true);

    ytBgm.syncSameTrack("dQw4w9WgXcQ", 55.2, true);

    expect(player.seekTo).toHaveBeenCalledWith(55.2, true);
    expect(player.loadVideoById).not.toHaveBeenCalled();
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
});

describe("play_yt wire args", () => {
  it("main.ts parses fractional offset from OOB args[1]", () => {
    const args = ["dQw4w9WgXcQ", 33.75, 1];
    expect(parseFloat(String(args[1] ?? 0))).toBe(33.75);
  });
});
