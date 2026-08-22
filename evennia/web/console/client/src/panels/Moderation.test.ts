import { fireEvent, render, waitFor } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { view } from "../lib/state.svelte";
import Moderation from "./Moderation.svelte";

const SESSION = {
  id: 7,
  account: "Sol",
  protocol: "webclient/websocket",
  connected: "2026-08-22T12:00:00+00:00",
  disconnected: "",
  ip: "withheld",
  address_state: "held",
  address_state_note: "",
  cidr: "203.0.113.0/24",
  asn: 64500,
  network: "Example Transit",
  country: "TR",
  is_datacenter: false,
  is_tor: true,
  client_name: "Mudlet",
  term: "xterm-256color",
  encoding: "utf-8",
  screen: "160 × 48",
  user_agent: "Mozilla/5.0 Test Browser",
  client_fp: "withheld",
  device_token: "withheld",
  signatures: [
    { field: "client_fp", label: "client capability", value: "withheld", present: true },
    { field: "device_token", label: "device token", value: "withheld", present: true },
    { field: "http_fp", label: "browser headers", value: "withheld", present: true },
  ],
  address_trustworthy: true,
  address_warning: "",
};

const DETAIL = {
  id: 7,
  account: "Sol",
  protocol: "webclient/websocket",
  groups: [
    {
      label: "Record",
      fields: [
        { name: "id", label: "ID", value: 7, recorded: true, sensitive: false },
        {
          name: "session_uid",
          label: "session uid",
          value: "session-uid",
          recorded: true,
          sensitive: false,
        },
      ],
    },
    {
      label: "Network and provenance",
      fields: [
        {
          name: "ip",
          label: "IP",
          value: "203.0.113.7",
          recorded: true,
          sensitive: true,
        },
        {
          name: "ip_hash",
          label: "IP hash",
          value: "ip-hash-exact",
          recorded: true,
          sensitive: true,
        },
      ],
    },
    {
      label: "Client",
      fields: [
        {
          name: "user_agent",
          label: "user agent",
          value: "Mozilla/5.0 Test Browser",
          recorded: true,
          sensitive: false,
        },
        {
          name: "client_fp",
          label: "client fp",
          value: "client-fingerprint-exact",
          recorded: true,
          sensitive: true,
        },
      ],
    },
    {
      label: "Identity signals",
      fields: [
        {
          name: "device_token",
          label: "device token",
          value: "device-token-exact",
          recorded: true,
          sensitive: true,
        },
        {
          name: "tls_sig",
          label: "TLS sig",
          value: "tls-fingerprint-exact",
          recorded: true,
          sensitive: true,
        },
      ],
    },
    {
      label: "Negotiation and protocol flags",
      fields: [
        {
          name: "flags",
          label: "flags",
          value: { CLIENTNAME: "Mudlet", ANSI: true },
          recorded: true,
          sensitive: false,
        },
      ],
    },
  ],
};

function response(payload: unknown): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => payload,
  } as unknown as Response;
}

function fetchFor(sessions: typeof SESSION[] = []) {
  return vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/rows/")) {
      return response({
        rows: {
          flags: [],
          sanctions: [],
          sessions,
          open_flags: 0,
          flag_states: [],
          levels: [],
          subject_types: [],
          note: "Detection writes flags. Only a person turns one into a sanction.",
        },
      });
    }
    if (url.includes("/actions/connection/")) return response({ result: DETAIL });
    if (url.includes("/actions/signals/")) {
      return response({ result: { sample: 0, rows: [], note: "No connection recorded yet." } });
    }
    if (url.includes("/actions/proposals/")) return response({ result: { rows: [] } });
    throw new Error(`Unexpected request: ${url}`);
  });
}

describe("Moderation connection history", () => {
  beforeEach(() => {
    view.modState = "";
    view.modAccount = "";
    view.modFlag = "";
  });

  afterEach(() => vi.unstubAllGlobals());

  it("shows readable client and GeoIP context, then every exact field on open", async () => {
    vi.stubGlobal("fetch", fetchFor([SESSION]));
    const { container, getByRole } = render(Moderation, {});

    await waitFor(() => expect(container.textContent).toContain("Example Transit"));
    expect(container.textContent).toContain("Mudlet");
    expect(container.textContent).toContain("xterm-256color");
    expect(container.textContent).toContain("160 × 48");
    expect(container.textContent).toContain("TR");
    expect(container.textContent).not.toContain("device-token-exact");

    await fireEvent.click(getByRole("button", { name: /open connection 7/i }));

    await waitFor(() => expect(container.textContent).toContain("device-token-exact"));
    expect(container.textContent).toContain("203.0.113.7");
    expect(container.textContent).toContain("ip-hash-exact");
    expect(container.textContent).toContain("client-fingerprint-exact");
    expect(container.textContent).toContain("tls-fingerprint-exact");
    expect(container.textContent).toContain('"CLIENTNAME": "Mudlet"');
  });

  it("does not waste the calm state on alarms and repeated empty sections", async () => {
    vi.stubGlobal("fetch", fetchFor());
    const { container } = render(Moderation, {});

    await waitFor(() => expect(container.textContent).toContain("Recent connections"));

    expect(container.querySelector(".annunciator")).toBeNull();
    expect(container.textContent).not.toContain("NO FLAG IS WAITING");
    expect(container.textContent).not.toContain("FLAGS AWAITING A PERSON");
    expect(container.textContent).not.toContain("NO ACTIVE SANCTIONS");
    expect(container.textContent).not.toContain("NOBODY HAS ASKED");
  });
});
