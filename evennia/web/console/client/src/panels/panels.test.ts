import { render, waitFor } from "@testing-library/svelte";
import { tick } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PANELS } from "../lib/registry";
import { session, view, URL_KEYS } from "../lib/state.svelte";

/* Every panel renders.
 *
 * This is the test the console did not have, and its absence is the whole
 * reason for the port. The flag dossier read `duration` and `reach` from
 * another function's scope, threw `ReferenceError` on the first click, and
 * shipped -- because nothing ever ran that code outside a browser. Nine hundred
 * service-side tests passed the entire time, correctly: the service was right.
 *
 * Two passes per panel. The first is the synchronous render, which is where a
 * scope error or a bad snippet argument shows up. The second is after the
 * stubbed reply lands, which is where a payload read against the wrong shape
 * shows up -- the other defect class this project keeps producing.
 *
 * The stub answers everything with an empty-but-well-formed payload, so what is
 * exercised is each panel's handling of "the server said nothing yet". That is
 * the state every panel is in for the first few hundred milliseconds an
 * operator looks at it, and the state a panel is stuck in during an outage.
 */

const EMPTY = {
  rows: {
    rows: [],
    flags: [],
    sanctions: [],
    sessions: [],
    columns: [],
    files: [],
    states: [],
    events: [],
    types: [],
    by_status: [],
    outcomes: [],
    panels: [],
    present: [],
    capabilities: [],
    views: [],
    models: [],
    fields: [],
    checks: {},
    alarms: [],
    caches: [],
    orphans: [],
    stored: [],
    findings: [],
    tables: [],
    indexes: [],
    long_running: [],
    connections: {},
    unused_indexes: {},
    systems: {},
    tasks: {},
    sample: {},
    backend: {},
    bus: {},
  },
  result: {
    rows: [],
    models: [],
    fields: [],
    accounts: [],
    keys: [],
    present: [],
    output: [],
  },
  record: { evidence: [], categories: [], diff: {} },
};

function stubFetch() {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => structuredClone(EMPTY),
  } as unknown as Response);
}

describe("every registered panel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", stubFetch());
    session.panels = [{ key: "records", label: "Records" }];
    session.current = "records";
    session.settings = {};
    for (const key of URL_KEYS) view[key] = "";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  for (const [key, Panel] of Object.entries(PANELS)) {
    it(`${key} renders without throwing`, async () => {
      const { container } = render(Panel, {});
      expect(container.querySelector(".panel-head, .panel-body")).not.toBeNull();

      // Let the stubbed reply land and the panel re-render against it.
      await tick();
      await Promise.resolve();
      await tick();

      expect(container.innerHTML.length).toBeGreaterThan(0);
    });
  }

  it("registers a component for every panel the engine ships", () => {
    // The console's own count. A panel added to the engine and never drawn
    // renders the "no view" notice, which is visible but is still a gap.
    expect(Object.keys(PANELS)).toHaveLength(23);
  });
});

describe("panels that open a record", () => {
  /* The exact shape of the defect: a detail view that is only reached by a
   * click, so it renders for the first time in front of an operator. */

  beforeEach(() => {
    vi.stubGlobal("fetch", stubFetch());
    for (const key of URL_KEYS) view[key] = "";
  });

  afterEach(() => vi.unstubAllGlobals());

  const OPENERS: [string, string, string][] = [
    ["moderation", "modFlag", "1"],
    ["errors", "errorOpen", "sig-1"],
    ["jobs", "jobOpen", "1"],
    ["eventbus", "busOpen", "1"],
    ["audit", "auditOpen", "1"],
    ["attributes", "attrObject", "1"],
    ["attributes", "attrKeyOpen", "desc"],
    ["records", "editing", "1"],
    ["moderation", "modAccount", "somebody"],
  ];

  for (const [panel, key, value] of OPENERS) {
    it(`${panel} renders with ${key} open`, async () => {
      (view as Record<string, string>)[key] = value;
      const { container } = render(PANELS[panel], {});
      await tick();
      await Promise.resolve();
      await tick();
      expect(container.innerHTML.length).toBeGreaterThan(0);
    });
  }
});

describe("a panel whose request fails", () => {
  /* An outage must not blank the station. The operator needs the alarm *and*
   * the last good reading, not the alarm and an empty region. */

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        headers: new Headers({ "X-Console-Outcome": "unavailable" }),
        json: async () => ({ detail: "the game server is not reachable" }),
      } as unknown as Response),
    );
    for (const key of URL_KEYS) view[key] = "";
  });

  afterEach(() => vi.unstubAllGlobals());

  for (const [key, Panel] of Object.entries(PANELS)) {
    it(`${key} survives a failed request`, async () => {
      const { container } = render(Panel, {});
      await tick();
      await Promise.resolve();
      await tick();
      expect(container.querySelector(".panel-head, .panel-body")).not.toBeNull();
    });
  }
});

describe("session watch status", () => {
  beforeEach(() => {
    for (const key of URL_KEYS) view[key] = "";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/actions/watches/")
          ? {
              result: {
                rows: [
                  {
                    watch_id: "watch-other",
                    sessid: 7,
                    account: "player",
                    watcher: "other staff",
                    reason: "a report",
                    seconds_left: 120,
                    mine: false,
                  },
                ],
              },
            }
          : { rows: { rows: [] } };
        return {
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => payload,
        } as unknown as Response;
      }),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("unwraps action results and shows another operator's active watch", async () => {
    const { container } = render(PANELS.sessions, {});
    await waitFor(() => expect(container.textContent).toContain("other staff"));
    expect(container.textContent).toContain("player");
    expect(container.textContent).not.toContain("YOUR LIVE TRANSCRIPT");
  });
});
