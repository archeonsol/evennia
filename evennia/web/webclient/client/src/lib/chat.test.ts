import { beforeEach, describe, expect, it } from "vitest";
import { chat } from "./chat.svelte";

describe("chat staff role", () => {
  beforeEach(() => {
    chat.staff = false;
    chat.staffKnown = false;
    chat.tickets = [];
  });

  it("takes the server's answer over an inbox that arrived earlier", () => {
    // A stray inbox used to make a session staff for good.
    chat.handleOob("ticket_inbox", [], { tickets: [] });
    expect(chat.staff).toBe(true);
    chat.handleOob("ticket_role", [], { staff: false });
    expect(chat.staff).toBe(false);
    expect(chat.staffKnown).toBe(true);
  });

  it("marks staff when the server says so", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    expect(chat.staff).toBe(true);
    expect(chat.staffKnown).toBe(true);
  });

  it("a stray inbox after the role is known does not make a player staff", () => {
    chat.handleOob("ticket_role", [], { staff: false });
    chat.handleOob("ticket_inbox", [], { tickets: [] });
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
