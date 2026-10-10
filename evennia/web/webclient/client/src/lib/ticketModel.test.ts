import { describe, expect, it } from "vitest";

import {
  ageFromMinutes,
  ageSince,
  byServerOrder,
  clockClass,
  clockText,
  compareSort,
  dotClass,
  inMineView,
  inView,
  isFaint,
  isHot,
  matches,
  mineCounts,
  playerState,
  presenceText,
  priorityWord,
  remove,
  search,
  sentence,
  step,
  upsert,
  viewCounts,
  whoIs,
  withPresence,
  type TicketRow,
} from "./ticketModel";

function row(over: Partial<TicketRow> = {}): TicketRow {
  return {
    id: "a",
    number: 1,
    ref: "#1",
    kind: "request",
    label: "Player Request",
    approvable: false,
    status: "pending",
    state: "unanswered",
    presence: "online",
    seen: 0,
    account_id: 10,
    account_name: "kade",
    account_online: true,
    requester_name: "Kade",
    assignee: "",
    assignee_id: null,
    priority: 0,
    created: 100,
    updated: 100,
    unanswered_since: 100,
    answered_at: 0,
    waited_minutes: 0,
    clock: "ok",
    preview: "",
    title: "The door",
    subject: "The door",
    sort: [0, 2, 0, -100, -1],
    ...over,
  };
}

describe("the server's order", () => {
  it("compares keys element by element, and a missing element is zero", () => {
    expect(compareSort([0, 1], [0, 2])).toBe(-1);
    expect(compareSort([1], [0, 5])).toBe(1);
    expect(compareSort([0, 0], [0])).toBe(0);
    expect(compareSort(undefined, [0])).toBe(0);
    expect(compareSort(undefined, [1])).toBe(-1);
  });

  it("falls back to newest first when two rows have no key", () => {
    const older = row({ id: "o", sort: undefined, created: 1 });
    const newer = row({ id: "n", sort: undefined, created: 2 });
    expect([older, newer].sort(byServerOrder).map((r) => r.id)).toEqual(["n", "o"]);
  });

  it("puts a changed row where its key says and replaces the old one", () => {
    const rows = [row({ id: "a", sort: [0, 2, 0, -3, -3] }), row({ id: "b", sort: [0, 2, 0, -2, -2] })];
    const next = upsert(rows, row({ id: "a", sort: [0, 0, 0, 5, 1], title: "Moved" }));
    expect(next.map((r) => r.id)).toEqual(["a", "b"]);
    expect(next).toHaveLength(2);
    expect(next[0].title).toBe("Moved");
    const added = upsert(rows, row({ id: "c", sort: [0, 1, 0, 1, 1] }));
    expect(added.map((r) => r.id)).toEqual(["c", "a", "b"]);
  });

  it("leaves the list alone when asked to remove a row it does not hold", () => {
    const rows = [row()];
    expect(remove(rows, "zzz")).toBe(rows);
    expect(remove(rows, "a")).toEqual([]);
  });
});

describe("a player coming and going", () => {
  const rows = [row({ id: "a", presence: "offline", sort: [0, 4, 0, 1, 1] }), row({ id: "b", presence: "online", sort: [0, 1, 0, 2, 2] })];

  it("moves one row to the top when its player logs in", () => {
    const next = withPresence(rows, { id: "a", presence: "online", seen: 0, sort: [0, 1, 0, 1, 1], account_online: true });
    expect(next.map((r) => r.id)).toEqual(["a", "b"]);
    expect(next[0].presence).toBe("online");
    expect(next[0].account_online).toBe(true);
  });

  it("sinks a row when its player leaves", () => {
    const next = withPresence(rows, { id: "b", presence: "away", seen: 50, sort: [0, 3, 0, 2, 2] });
    // Away still sits above a player who has been gone longer.
    expect(next.map((r) => r.id)).toEqual(["b", "a"]);
    expect(next.find((r) => r.id === "b")?.seen).toBe(50);
  });

  it("ignores a row that is not in the list", () => {
    expect(withPresence(rows, { id: "nope", presence: "room" })).toBe(rows);
  });
});

describe("the views", () => {
  const rows = [
    row({ id: "m", assignee_id: 7, assignee: "Mira", presence: "offline" }),
    row({ id: "u", status: "pending", presence: "room" }),
    row({ id: "w", status: "waiting", state: "answered", presence: "online" }),
  ];

  it("filters by what staff mean", () => {
    expect(rows.filter((r) => inView(r, "mine", 7)).map((r) => r.id)).toEqual(["m"]);
    expect(rows.filter((r) => inView(r, "unanswered", 7)).map((r) => r.id)).toEqual(["m", "u"]);
    expect(rows.filter((r) => inView(r, "answered", 7)).map((r) => r.id)).toEqual(["w"]);
    expect(rows.filter((r) => inView(r, "online", 7)).map((r) => r.id)).toEqual(["u", "w"]);
    expect(rows.filter((r) => inView(r, "all", 7))).toHaveLength(3);
  });

  it("holds nothing as mine when the account is not known yet", () => {
    expect(rows.some((r) => inView(r, "mine", null))).toBe(false);
  });

  it("counts every view over the whole list", () => {
    expect(viewCounts(rows, 7)).toEqual({ mine: 1, unanswered: 2, answered: 1, online: 2, all: 3 });
  });
});

describe("search", () => {
  const rows = [
    row({ id: "a", number: 1042, ref: "#1042", title: "Dov Kessler, the scarred broker", requester_name: "Mira Thane", label: "Puppet Request" }),
    row({ id: "b", number: 1043, ref: "#1043", title: "Cannot open my apartment door", requester_name: "Dena", preview: "the rent shows paid" }),
  ];

  it("finds a ticket by number with or without the hash", () => {
    expect(search(rows, "1042").map((r) => r.id)).toEqual(["a"]);
    expect(search(rows, "#1043").map((r) => r.id)).toEqual(["b"]);
  });

  it("finds by who, what and the kind", () => {
    expect(search(rows, "mira").map((r) => r.id)).toEqual(["a"]);
    expect(search(rows, "apartment").map((r) => r.id)).toEqual(["b"]);
    expect(search(rows, "rent shows").map((r) => r.id)).toEqual(["b"]);
    expect(search(rows, "puppet").map((r) => r.id)).toEqual(["a"]);
  });

  it("finds nothing for words nobody wrote, and everything for no words", () => {
    expect(search(rows, "zebra")).toEqual([]);
    expect(search(rows, "  ")).toBe(rows);
    expect(matches(rows[0], "")).toBe(true);
  });
});

describe("how long, in words", () => {
  it("gives now, minutes, hours and days", () => {
    expect(ageFromMinutes(0)).toBe("now");
    expect(ageFromMinutes(7)).toBe("7m");
    expect(ageFromMinutes(59)).toBe("59m");
    expect(ageFromMinutes(60)).toBe("1h");
    expect(ageFromMinutes(60 * 23)).toBe("23h");
    expect(ageFromMinutes(60 * 24 * 2)).toBe("2d");
    expect(ageFromMinutes(-5)).toBe("now");
  });

  it("measures from a time in seconds, and says nothing for no time", () => {
    expect(ageSince(1000 - 7 * 60, 1000)).toBe("7m");
    expect(ageSince(0, 1000)).toBe("");
  });

  it("shows the wait while unanswered and the last activity after", () => {
    expect(clockText(row({ unanswered_since: 1000 - 120, updated: 1 }), 1000)).toBe("2m");
    expect(clockText(row({ status: "waiting", unanswered_since: 0, updated: 1000 - 3600 }), 1000)).toBe("1h");
  });

  it("colours only a late unanswered ticket", () => {
    expect(clockClass(row({ clock: "ok" }))).toBe("");
    expect(clockClass(row({ clock: "overdue" }))).toBe("warn");
    expect(clockClass(row({ clock: "long" }))).toBe("bad");
    expect(clockClass(row({ status: "waiting", clock: "long" }))).toBe("");
  });
});

describe("who is around, in words", () => {
  const base = { requester_name: "Mira Thane", account_name: "mthane", account_online: false, seen: 0 };

  it("names where the player is", () => {
    expect(presenceText({ ...base, presence: "room" }, 1000)).toBe("Mira Thane is in the room");
    expect(presenceText({ ...base, presence: "online" }, 1000)).toBe("Mira Thane is online");
    expect(presenceText({ ...base, presence: "offline" }, 1000)).toBe("Mira Thane is offline");
  });

  it("says how long ago a player left", () => {
    expect(presenceText({ ...base, presence: "away", seen: 1000 - 4 * 60 }, 1000)).toBe("Mira Thane left 4m ago");
    expect(presenceText({ ...base, presence: "away", seen: 0 }, 1000)).toBe("Mira Thane just left");
    expect(presenceText({ ...base, presence: "away", seen: 1000 }, 1000)).toBe("Mira Thane just left");
  });

  it("falls back to the account when the server did not say", () => {
    expect(presenceText({ ...base, presence: "", account_online: true }, 1000)).toBe("Mira Thane is online");
    expect(whoIs({ requester_name: "", account_name: "mthane" })).toBe("mthane");
    expect(whoIs({ requester_name: "", account_name: "" })).toBe("Someone");
  });

  it("draws one dot for each state", () => {
    expect(dotClass({ presence: "room", account_online: false })).toBe("here");
    expect(dotClass({ presence: "online", account_online: false })).toBe("on");
    expect(dotClass({ presence: "", account_online: true })).toBe("on");
    expect(dotClass({ presence: "away", account_online: false })).toBe("away");
    expect(dotClass({ presence: "offline", account_online: false })).toBe("off");
  });

  it("fades a puppet request whose player has gone, and nothing else", () => {
    expect(isFaint({ kind: "puppet", presence: "offline", status: "pending" })).toBe(true);
    expect(isFaint({ kind: "puppet", presence: "away", status: "pending" })).toBe(true);
    expect(isFaint({ kind: "puppet", presence: "online", status: "pending" })).toBe(false);
    expect(isFaint({ kind: "request", presence: "offline", status: "pending" })).toBe(false);
    expect(isFaint({ kind: "puppet", presence: "offline", status: "waiting" })).toBe(false);
  });

  it("marks urgent and well late rows as hot", () => {
    expect(isHot({ priority: 2, clock: "ok", status: "pending" })).toBe(true);
    expect(isHot({ priority: 0, clock: "long", status: "pending" })).toBe(true);
    expect(isHot({ priority: 0, clock: "overdue", status: "pending" })).toBe(false);
    expect(isHot({ priority: 3, clock: "long", status: "waiting" })).toBe(false);
  });
});

describe("words and keys", () => {
  it("names a priority and clamps the rest", () => {
    expect(priorityWord(0)).toBe("Low");
    expect(priorityWord(3)).toBe("Urgent");
    expect(priorityWord(9)).toBe("Urgent");
    expect(priorityWord(-2)).toBe("Low");
  });

  it("steps through the list and stays at the ends", () => {
    const ids = ["a", "b", "c"];
    expect(step(ids, null, 1)).toBe("a");
    expect(step(ids, null, -1)).toBe("c");
    expect(step(ids, "a", 1)).toBe("b");
    expect(step(ids, "c", 1)).toBe("c");
    expect(step(ids, "a", -1)).toBe("a");
    expect(step([], "a", 1)).toBeNull();
    expect(step(ids, "gone", 1)).toBe("a");
  });

  it("capitalises a state for display", () => {
    expect(sentence("unanswered")).toBe("Unanswered");
    expect(sentence("to review")).toBe("To review");
    expect(sentence("")).toBe("");
    expect(sentence(undefined)).toBe("");
  });
});

describe("a player's own requests", () => {
  const rows = [
    row({ id: "o", status: "pending" }),
    row({ id: "a", status: "waiting", unread: true }),
    row({ id: "c", status: "closed" }),
  ];

  it("reads a status as a plain word", () => {
    expect(playerState({ status: "pending" })).toBe("Open");
    expect(playerState({ status: "waiting" })).toBe("Answered");
    expect(playerState({ status: "closed" })).toBe("Closed");
    expect(playerState({ status: "withdrawn" })).toBe("Withdrawn");
    expect(playerState({ status: "approved" })).toBe("Approved");
    expect(playerState({ status: "resolved" })).toBe("Closed");
  });

  it("sorts requests into the views the player sees", () => {
    expect(rows.filter((r) => inMineView(r, "open")).map((r) => r.id)).toEqual(["o", "a"]);
    expect(rows.filter((r) => inMineView(r, "answered")).map((r) => r.id)).toEqual(["a"]);
    expect(rows.filter((r) => inMineView(r, "all"))).toHaveLength(3);
    expect(mineCounts(rows)).toEqual({ open: 2, answered: 1, all: 3, unread: 1 });
  });
});
