import { describe, expect, it } from "vitest";
import { sortTickets } from "./ticketSort";

// A parked high-priority ticket, an old low one with a fresh reply, and a
// brand-new low one: the case that hid new tickets at the bottom.
const parked = { id: "parked", priority: 10, created: 200, updated: 200 };
const replied = { id: "replied", priority: 0, created: 100, updated: 900 };
const fresh = { id: "fresh", priority: 0, created: 500, updated: 500 };
const rows = [parked, replied, fresh];
const ids = (r: { id: string }[]) => r.map((t) => t.id);

describe("sortTickets", () => {
  it("puts the newest ticket first whatever its priority", () => {
    expect(ids(sortTickets(rows, "newest"))).toEqual(["fresh", "parked", "replied"]);
  });

  it("puts the latest message first", () => {
    expect(ids(sortTickets(rows, "activity"))).toEqual(["replied", "fresh", "parked"]);
  });

  it("puts the oldest ticket first", () => {
    expect(ids(sortTickets(rows, "oldest"))).toEqual(["replied", "parked", "fresh"]);
  });

  it("leads by priority, newest first within one", () => {
    expect(ids(sortTickets(rows, "priority"))).toEqual(["parked", "fresh", "replied"]);
  });

  it("leaves the input order alone", () => {
    sortTickets(rows, "oldest");
    expect(ids(rows)).toEqual(["parked", "replied", "fresh"]);
  });
});
