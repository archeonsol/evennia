import { beforeEach, describe, expect, it } from "vitest";
import { chat } from "./chat.svelte";
import { tickets } from "./tickets.svelte";

describe("chat staff role", () => {
  beforeEach(() => {
    tickets.staff = false;
    tickets.staffKnown = false;
  });

  it("follows the ticket role the server states", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    expect(chat.staff).toBe(true);
    expect(chat.staffKnown).toBe(true);
    chat.handleOob("ticket_role", [], { staff: false });
    expect(chat.staff).toBe(false);
  });

  it("does not know the role before the server has said", () => {
    expect(chat.staffKnown).toBe(false);
  });

  // Last: an assist inbox marks the store an assist viewer for good, which is
  // the point, and would leak into any test after it.
  it("keeps an assist viewer on the staff side", () => {
    chat.handleOob("assist_inbox", [], { threads: [] });
    chat.handleOob("ticket_role", [], { staff: false });
    expect(chat.staff).toBe(true);
  });
});

describe("tab badges", () => {
  beforeEach(() => {
    chat.unread = {};
  });

  it("totals unread across channels", () => {
    chat.handleOob("channel_unread", [], { help: 2, nous: 3 });
    expect(chat.channelsUnseen).toBe(5);
  });
});
