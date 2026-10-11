// The ticket rows the server sends, and the few rules both ticket panels share.
//
// Pure functions, no state: the panels and the store call these, and the tests
// call them directly. The server decides the order of the queue (one rule, the
// same on the terminal, the web board and here), so nothing in this file sorts by
// anything but the key the server sent.

/** A row of the staff queue or of a player's own list, as the server serialises it. */
export interface TicketRow {
  id: string;
  number: number | null;
  /** How people say it: "#1042". */
  ref: string;
  short_id?: string;
  kind: string;
  label: string;
  approvable: boolean;
  /** The stored status: pending, waiting, closed, approved, denied, withdrawn. */
  status: string;
  /** The word for it: unanswered, answered, to review, closed... */
  state: string;
  /** room, online, away or offline. Staff rows only. */
  presence: string;
  /** When the player was last seen, in seconds. */
  seen: number;
  account_id: number | null;
  account_name: string;
  account_online: boolean;
  requester_name: string;
  assignee: string;
  assignee_id: number | null;
  priority: number;
  created: number;
  updated: number;
  unanswered_since: number;
  answered_at: number;
  waited_minutes: number;
  /** ok, overdue (past its kind's time) or long (well past it). */
  clock: string;
  unread?: boolean;
  linked?: boolean;
  archived?: boolean;
  preview: string;
  title: string;
  subject: string;
  /** The server's order key: lower sorts first. */
  sort?: number[];
  [key: string]: unknown;
}

export type ViewKey = "mine" | "unanswered" | "answered" | "online" | "all";

export const VIEW_LABELS: Record<ViewKey, string> = {
  mine: "Mine",
  unanswered: "Unanswered",
  answered: "Answered",
  online: "Online",
  all: "All",
};

export const OPEN_STATUSES = ["pending", "waiting"];

export function isOpen(status: string | undefined): boolean {
  return !!status && OPEN_STATUSES.includes(status);
}

/** Compare two server order keys, element by element. */
export function compareSort(a?: number[], b?: number[]): number {
  const x = a ?? [];
  const y = b ?? [];
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) {
    const p = x[i] ?? 0;
    const q = y[i] ?? 0;
    if (p !== q) return p < q ? -1 : 1;
  }
  return 0;
}

/** The queue's order: the server's key, then newest first when it has none. */
export function byServerOrder(a: TicketRow, b: TicketRow): number {
  const bySort = compareSort(a.sort, b.sort);
  if (bySort) return bySort;
  return (b.created ?? 0) - (a.created ?? 0);
}

/** Add a row or replace the one with the same id, keeping the queue in order. */
export function upsert(rows: TicketRow[], row: TicketRow): TicketRow[] {
  const next = rows.filter((r) => r.id !== row.id);
  next.push(row);
  return next.sort(byServerOrder);
}

export function remove(rows: TicketRow[], id: string): TicketRow[] {
  return rows.some((r) => r.id === id) ? rows.filter((r) => r.id !== id) : rows;
}

export interface PresencePatch {
  id: string;
  presence?: string;
  seen?: number;
  sort?: number[];
  account_online?: boolean;
}

/** A player came, went or moved: update that one row and re-order. */
export function withPresence(rows: TicketRow[], patch: PresencePatch): TicketRow[] {
  if (!rows.some((r) => r.id === patch.id)) return rows;
  const next = rows.map((r) =>
    r.id === patch.id
      ? {
          ...r,
          presence: patch.presence ?? r.presence,
          seen: patch.seen ?? r.seen,
          sort: patch.sort ?? r.sort,
          account_online: patch.account_online ?? r.account_online,
        }
      : r,
  );
  return next.sort(byServerOrder);
}

/** Whether a row belongs in one of the staff views. */
export function inView(row: TicketRow, view: ViewKey, me: number | null): boolean {
  switch (view) {
    case "mine":
      return me != null && row.assignee_id === me;
    case "unanswered":
      return row.status === "pending";
    case "answered":
      return row.status === "waiting";
    case "online":
      return row.presence === "room" || row.presence === "online";
    default:
      return true;
  }
}

export function viewCounts(rows: TicketRow[], me: number | null): Record<ViewKey, number> {
  const out: Record<ViewKey, number> = { mine: 0, unanswered: 0, answered: 0, online: 0, all: rows.length };
  for (const row of rows) {
    for (const key of ["mine", "unanswered", "answered", "online"] as const) {
      if (inView(row, key, me)) out[key] += 1;
    }
  }
  return out;
}

/** The words a search matches on: the number, who, what and where it stands. */
export function matches(row: TicketRow, query: string): boolean {
  const q = query.trim().toLowerCase().replace(/^#/, "");
  if (!q) return true;
  const fields = [
    String(row.number ?? ""),
    row.ref,
    row.title,
    row.subject,
    row.requester_name,
    row.account_name,
    row.preview,
    row.label,
    row.assignee,
    row.state,
  ];
  return fields.some((v) => String(v ?? "").toLowerCase().replace(/^#/, "").includes(q));
}

export function search(rows: TicketRow[], query: string): TicketRow[] {
  return query.trim() ? rows.filter((r) => matches(r, query)) : rows;
}

/** "now", "7m", "3h", "2d": how long, for a column. */
export function ageFromMinutes(minutes: number): string {
  const m = Math.max(0, Math.floor(minutes || 0));
  if (m < 1) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`;
}

export function ageSince(ts: number, nowSeconds: number): string {
  if (!ts) return "";
  return ageFromMinutes((nowSeconds - ts) / 60);
}

/** What a row shows in its clock column: the wait while unanswered, else the last activity. */
export function clockText(row: TicketRow, nowSeconds: number): string {
  if (row.status === "pending" && row.unanswered_since) return ageSince(row.unanswered_since, nowSeconds);
  return ageSince(row.updated, nowSeconds);
}

/** The colour class of the clock: only late tickets are coloured. */
export function clockClass(row: TicketRow): "" | "warn" | "bad" {
  if (row.status !== "pending") return "";
  return row.clock === "long" ? "bad" : row.clock === "overdue" ? "warn" : "";
}

/** Who asked, as a name to put in a sentence. */
export function whoIs(row: Pick<TicketRow, "requester_name" | "account_name">): string {
  return row.requester_name || row.account_name || "Someone";
}

/** Whether the player is around, in plain words. */
export function presenceText(
  row: Pick<TicketRow, "requester_name" | "account_name" | "presence" | "seen" | "account_online">,
  nowSeconds: number,
): string {
  const who = whoIs(row);
  switch (row.presence) {
    case "room":
      return `${who} is in the room`;
    case "online":
      return `${who} is online`;
    case "away": {
      const ago = row.seen ? ageSince(row.seen, nowSeconds) : "";
      return ago && ago !== "now" ? `${who} left ${ago} ago` : `${who} just left`;
    }
    default:
      return row.account_online ? `${who} is online` : `${who} is offline`;
  }
}

/** The dot beside a row: room, online, away or off. */
export function dotClass(row: Pick<TicketRow, "presence" | "account_online">): "here" | "on" | "away" | "off" {
  if (row.presence === "room") return "here";
  if (row.presence === "online" || (!row.presence && row.account_online)) return "on";
  if (row.presence === "away") return "away";
  return "off";
}

/** A puppet request whose player is not around sinks, and fades. */
export function isFaint(row: Pick<TicketRow, "kind" | "presence" | "status">): boolean {
  return row.kind === "puppet" && row.status === "pending" && (row.presence === "away" || row.presence === "offline");
}

/** A row that wants the eye: urgent, or well past its time. */
export function isHot(row: Pick<TicketRow, "priority" | "clock" | "status">): boolean {
  return row.status === "pending" && ((row.priority ?? 0) >= 2 || row.clock === "long");
}

export const PRIORITY_WORDS = ["Low", "Normal", "High", "Urgent"];

export function priorityWord(priority: number): string {
  return PRIORITY_WORDS[Math.max(0, Math.min(PRIORITY_WORDS.length - 1, priority ?? 0))];
}

/** Move a selection by one through a list of ids; stays put at the ends. */
export function step(ids: string[], current: string | null, delta: number): string | null {
  if (!ids.length) return null;
  const at = current ? ids.indexOf(current) : -1;
  if (at < 0) return ids[delta < 0 ? ids.length - 1 : 0];
  return ids[Math.max(0, Math.min(ids.length - 1, at + delta))];
}

/** Capitalise the first letter: the server sends states in lower case. */
export function sentence(text: string | undefined): string {
  const t = String(text ?? "");
  return t ? t[0].toUpperCase() + t.slice(1) : "";
}

/** The plain word a player reads for where a request stands. */
export function playerState(row: Pick<TicketRow, "status">): string {
  switch (row.status) {
    case "pending":
      return "Open";
    case "waiting":
      return "Answered";
    case "approved":
      return "Approved";
    case "denied":
      return "Denied";
    case "withdrawn":
      return "Withdrawn";
    default:
      return "Closed";
  }
}

export type MineView = "open" | "answered" | "all";

/** Whether one of a player's own requests belongs in a view. */
export function inMineView(row: Pick<TicketRow, "status">, view: MineView): boolean {
  if (view === "all") return true;
  if (view === "answered") return row.status === "waiting";
  return isOpen(row.status);
}

/** Counts for the player's views. */
export function mineCounts(rows: TicketRow[]): Record<MineView, number> & { unread: number } {
  return {
    open: rows.filter((r) => inMineView(r, "open")).length,
    answered: rows.filter((r) => inMineView(r, "answered")).length,
    all: rows.length,
    unread: rows.filter((r) => r.unread).length,
  };
}
