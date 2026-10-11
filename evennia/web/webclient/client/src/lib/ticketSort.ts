// Orders a staff member can pick for their own view of the ticket queue.
// "queue" is the server's order, the one telnet, the board and Discord show.
// `created` and `updated` are epoch seconds; `updated` moves on every message.

export type TicketSort = "queue" | "newest" | "activity" | "oldest" | "priority";

export const TICKET_SORTS: { key: TicketSort; label: string }[] = [
  { key: "queue", label: "Queue order" },
  { key: "newest", label: "Newest" },
  { key: "activity", label: "Last activity" },
  { key: "oldest", label: "Oldest" },
  { key: "priority", label: "Priority" },
];

const SORT_KEY = "underspire.tickets.sort.v1";

interface Sortable {
  created?: number;
  updated?: number;
  priority?: number;
}

const newest = (a: Sortable, b: Sortable) => (b.created ?? 0) - (a.created ?? 0);

const ORDERS: Record<TicketSort, (a: Sortable, b: Sortable) => number> = {
  // Array sort is stable, so rows that all compare equal keep the server's order.
  queue: () => 0,
  newest,
  activity: (a, b) => (b.updated ?? b.created ?? 0) - (a.updated ?? a.created ?? 0) || newest(a, b),
  oldest: (a, b) => -newest(a, b),
  priority: (a, b) => (b.priority ?? 0) - (a.priority ?? 0) || newest(a, b),
};

/** A sorted copy of `rows`. */
export function sortTickets<T extends Sortable>(rows: readonly T[], order: TicketSort): T[] {
  return [...rows].sort(ORDERS[order] ?? ORDERS.queue);
}

export function loadTicketSort(): TicketSort {
  try {
    const saved = localStorage.getItem(SORT_KEY) as TicketSort | null;
    return saved && saved in ORDERS ? saved : "queue";
  } catch {
    return "queue";
  }
}

export function saveTicketSort(order: TicketSort): void {
  try {
    localStorage.setItem(SORT_KEY, order);
  } catch {
    // Private windows refuse storage; the order then lasts the session.
  }
}
