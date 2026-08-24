import { fireEvent, render, waitFor } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Authorization from "./Authorization.svelte";

const PAYLOAD = {
  rows: {
    rows: [],
    fields: [],
    views: ["grants"],
    view: "grants",
    model: "server.authorizationgrant",
    capabilities: [
      {
        key: "underspire.ticket.assist",
        category: "Support",
        description: "Handle ordinary support tickets.",
        status: "active",
        default_visible: true,
      },
      {
        key: "engine.runtime.manage",
        category: "Runtime",
        description: "Control the running server.",
        status: "active",
        default_visible: false,
      },
      {
        key: "underspire.world.author",
        category: "Legacy",
        description: "Compatibility world authority.",
        status: "legacy",
        default_visible: false,
      },
    ],
    bundles: [
      {
        key: "underspire_chorus_neophyte",
        category: "Staff ranks",
        description: "Front-line Chorus staff.",
        status: "active",
        capabilities: ["underspire.ticket.assist"],
        default_visible: true,
      },
      {
        key: "runtime_operator",
        category: "Engine operations",
        description: "Server owner authority.",
        status: "active",
        capabilities: ["engine.runtime.manage"],
        default_visible: false,
      },
    ],
  },
};

describe("authorization vocabulary", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: new Headers(),
        json: async () => structuredClone(PAYLOAD),
      } as unknown as Response),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("shows active game grants first and reveals system vocabulary on request", async () => {
    const screen = render(Authorization, {});

    await waitFor(() => expect(screen.getAllByText("underspire.ticket.assist")).toBeTruthy());
    expect(screen.queryByText("engine.runtime.manage")).toBeNull();
    expect(screen.queryByText("underspire.world.author")).toBeNull();
    expect(screen.getByText("underspire_chorus_neophyte")).toBeTruthy();
    expect(screen.queryByText("runtime_operator")).toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: "Show system and legacy grants" }));

    expect(screen.getAllByText("engine.runtime.manage")).toBeTruthy();
    expect(screen.getByText("underspire.world.author")).toBeTruthy();
    expect(screen.getByText("runtime_operator")).toBeTruthy();
  });
});
