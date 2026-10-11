// The ticket data layer: the staff queue, the open ticket, and a player's own requests.
//
// Two protocols reach this store. A client that asks for the lean one with the
// `ticket_hello` request is sent one small event per change (`ticket_upsert`,
// `ticket_remove`, `ticket_presence`) and the queue it holds is already in the
// server's order. An older server, or a tab that has not said hello yet, is sent
// the whole queue as `ticket_inbox` after every change; that still works and
// replaces the list. Either way the order is the server's: nothing here sorts a
// queue by anything but the key the server sent (see ticketModel.ts).
//
// Every action is a request that answers with the ticket as it now stands, so no
// button types a command and nothing it confirms lands in the terminal.

import { connection } from "./evennia.svelte";
import { notify } from "./notify.svelte";
import { DEFAULT_FORM, DEFAULT_PRIORITIES, mergeForm, type PriorityGuide, type RequestKind, type TicketForm } from "./ticketForm";
import { toasts } from "./toasts.svelte";
import {
  byServerOrder,
  presenceText,
  remove,
  upsert,
  whoIs,
  withPresence,
  type PresencePatch,
  type TicketRow,
  type ViewKey,
} from "./ticketModel";

/** What the queue badge has been shown, kept per account (`<key>:<account id>`), so two people sharing a browser do not mark each other's tickets seen. */
export const QUEUE_SEEN_KEY = "underspire.queue.seen.v2";
/** Kept by the client that read a player's replies from the browser. The server holds that mark now; nothing writes this, but a config export still leaves it out. */
export const SEEN_KEY = "underspire.tickets.seen.v2";
/** Set per account once its layout has been given the Assist panel, so closing it sticks. */
export const ASSIST_ADDED_KEY = "underspire.assist.added";
/** Seen maps from before they were kept per account; they hold the last user's ticket ids. */
export const LEGACY_SEEN_KEYS = ["underspire.tickets.seen.v1", "underspire.queue.seen.v1"];
const VIEW_KEY = "underspire.tickets.view.v2";
const VIEWS: ViewKey[] = ["mine", "unanswered", "answered", "online", "all"];
/** The forms the game may name in `ticket_compose`. */
const COMPOSE_KINDS: RequestKind[] = ["request", "bug", "report", "puppet"];

/** Which tab of the Assist panel is showing: the caller's own requests, or the staff queue. */
export type AssistTab = "mine" | "queue";

/** The answer to a ticket action, for the panel to show. */
export interface TicketResult {
  ok: boolean;
  message: string;
  /** What puppeting answered: whether we were moved to the NPC, and the command that enters it. */
  puppet?: { ok: boolean; message: string; command: string } | null;
}

/** A saved reply, as the server lists it. */
export interface SavedReply {
  id: number;
  name: string;
  body: string;
  shared: boolean;
  mine: boolean;
}

/** What a new request carries, by kind. */
export interface NewRequest {
  kind: RequestKind;
  subject?: string;
  title?: string;
  text?: string;
  category?: string;
  severity?: string;
  npc?: string;
  said?: string;
  goal?: string;
  contact?: string;
}

function loadQueueSeen(account: number | null): Record<string, number> {
  if (account == null) return {};
  try {
    return JSON.parse(localStorage.getItem(`${QUEUE_SEEN_KEY}:${account}`) || "{}");
  } catch {
    return {};
  }
}

function saveQueueSeen(account: number | null, map: Record<string, number>): void {
  if (account == null) return;
  try {
    localStorage.setItem(`${QUEUE_SEEN_KEY}:${account}`, JSON.stringify(map));
  } catch {
    /* ignore */
  }
}

function loadView(): ViewKey {
  try {
    const saved = localStorage.getItem(VIEW_KEY) as ViewKey | null;
    if (saved && VIEWS.includes(saved)) return saved;
  } catch {
    /* a private window: use the default */
  }
  return "unanswered";
}

function fail(e: any, fallback = "That did not go through."): TicketResult {
  return { ok: false, message: String(e?.message || fallback) };
}

class Tickets {
  // -- who this tab is ------------------------------------------------------

  /** This session works the staff ticket queue. */
  staff = $state(false);
  /**
   * The server has said whether this session is staff. Until then `staff`
   * false only means "not yet known": a layout restored at page load keeps its
   * queue panel until the answer arrives.
   */
  staffKnown = $state(false);
  accountId = $state<number | null>(null);
  /** Whether this staff member is pinged about new tickets. */
  duty = $state(true);
  /** Whether the History view is theirs (the capability that closes tickets). */
  canHistory = $state(false);
  /** The server sends this tab one event per change, not the whole queue. */
  lean = $state(false);

  // -- the staff queue ------------------------------------------------------

  /** Open tickets, in the server's order. */
  rows = $state<TicketRow[]>([]);
  /** The ticket open in the workbench, with its conversation. */
  ticket = $state<any | null>(null);
  /** Why the queue could not load, shown instead of "nothing here". */
  error = $state("");
  /** The view the staff member last chose; kept in this browser. */
  view = $state<ViewKey>(loadView());
  /** Closed tickets, loaded on demand for the History view. */
  history = $state<TicketRow[]>([]);
  historyMore = $state(false);
  historyCapped = $state(false);
  replies = $state<SavedReply[]>([]);
  /** What each priority means, as the game says. Staff only. */
  priorities = $state<PriorityGuide[]>(DEFAULT_PRIORITIES);
  /** Whether the first full queue has arrived, so later arrivals are news. */
  private loaded = false;

  // -- a player's own requests ---------------------------------------------

  myRows = $state<TicketRow[]>([]);
  myTicket = $state<any | null>(null);
  /** Why the player's list could not load; shown instead of "no requests". */
  myError = $state("");
  mySearch = $state("");
  /** Bumped when one of the player's requests changes, so the list reloads. */
  myRev = $state(0);
  /** What the new-request form says. The game's words, once it has answered. */
  form = $state<TicketForm>(DEFAULT_FORM);
  private formLoaded = false;

  // -- the Assist panel and its tab badge -----------------------------------

  /** The Assist panel's tab. A player only ever has "mine". */
  assistTab = $state<AssistTab>("mine");
  /** The Assist panel is the focused panel (set by the layout). */
  assistFocused = $state(false);
  /**
   * The game asked for the New request form (`@request` typed in the terminal).
   * My requests clears it as soon as it has opened the form, so a panel that is
   * not on screen yet shows the form when it mounts.
   */
  wantsNewRequest = $state(false);
  /** The form the game named (`@bug`, `@report`, `@puppetrequest`), or none for the picker. */
  composeKind = $state<RequestKind | "">("");
  /** What the queue badge has been shown, for this account. */
  queueSeen = $state<Record<string, number>>({});
  /** The staff ticket last asked for; an older answer arriving later is dropped. */
  private viewingId: string | null = null;
  /** Counts the times the store was emptied for a new login; an answer that began before one is dropped. */
  private epoch = 0;

  /** Opens a panel by view id. Set from main.ts: the dock imports the stores. */
  private openPanel: ((view: string) => void) | null = null;

  setPanelOpener(fn: (view: string) => void): void {
    this.openPanel = fn;
  }

  /** The queue is on screen, so news there is seen at once. */
  get queueActive(): boolean {
    return this.assistFocused && this.assistTab === "queue";
  }

  /** How many requests hold a reply the player has not read. */
  get myUnread(): number {
    return this.myRows.filter((r) => r.unread).length;
  }

  /** New or changed queue tickets the viewer has not opened. */
  get queueUnseen(): number {
    if (!this.staff) return 0;
    return this.rows.filter((t) => t.status === "pending" && (t.updated ?? 0) > (this.queueSeen[t.id] ?? 0)).length;
  }

  /** Show a tab of the Assist panel and bring the panel forward. */
  showTab(tab: AssistTab): void {
    this.assistTab = tab === "queue" && !this.staff ? "mine" : tab;
    this.openPanel?.("assist");
  }

  /**
   * Open a New request form in My requests, the panel brought forward: the picker, or
   * the form of one kind when the game names it. A name this client has no form for
   * shows the picker.
   */
  composeRequest(kind?: unknown): void {
    this.composeKind = COMPOSE_KINDS.includes(kind as RequestKind) ? (kind as RequestKind) : "";
    this.wantsNewRequest = true;
    this.showTab("mine");
  }

  /** Forget the last account's tickets, role and badges; a new login starts clean. */
  reset(): void {
    this.epoch += 1;
    this.staff = false;
    this.staffKnown = false;
    this.accountId = null;
    this.duty = true;
    this.canHistory = false;
    this.lean = false;
    this.rows = [];
    this.ticket = null;
    this.error = "";
    this.history = [];
    this.historyMore = false;
    this.historyCapped = false;
    this.replies = [];
    this.priorities = DEFAULT_PRIORITIES;
    this.loaded = false;
    this.myRows = [];
    this.myTicket = null;
    this.myError = "";
    this.mySearch = "";
    this.myRev = 0;
    this.form = DEFAULT_FORM;
    this.formLoaded = false;
    this.queueSeen = {};
    this.assistTab = "mine";
    this.wantsNewRequest = false;
    this.composeKind = "";
    this.viewingId = null;
    for (const key of LEGACY_SEEN_KEYS) {
      try {
        localStorage.removeItem(key);
      } catch {
        /* ignore */
      }
    }
  }

  setView(view: ViewKey): void {
    this.view = view;
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* ignore */
    }
  }

  // -- the connection -------------------------------------------------------

  /**
   * Ask for the lean protocol and the queue as it stands. Called on every
   * connect: the flag belongs to the session, and a new session starts without
   * it. A server that does not know the request keeps sending whole inboxes,
   * which is handled below, so a failure here is not an error.
   */
  async hello(): Promise<void> {
    const epoch = this.epoch;
    try {
      // `compose` says this client can open the New request form when the game asks
      // (`ticket_compose`), so `@request` is not sent to a client that would ignore it.
      const r = await connection.request<any>("tickets", "ticket_hello", { compose: true });
      if (epoch !== this.epoch) return;
      this.useAccount(typeof r?.account_id === "number" ? r.account_id : null);
      this.duty = r?.duty !== false;
      this.canHistory = !!r?.history;
      // The role the server pushed (ticket_role) stands; this answer states it
      // only when nothing else has, so a late answer cannot undo a quell.
      if (!this.staffKnown) {
        this.staff = !!r?.staff;
        this.staffKnown = true;
      }
      this.lean = Number(r?.v) >= 2;
      if (Array.isArray(r?.priorities) && r.priorities.length) this.priorities = r.priorities;
      // A new session may be a new game build: ask for the form's words again.
      this.formLoaded = false;
      if (this.staff && Array.isArray(r?.tickets)) this.setRows(r.tickets, false);
      this.error = "";
    } catch {
      this.lean = false;
    }
  }

  /**
   * The server's answer on login, on every channel resync, and on @quell and
   * @unquell. Reading staff from an inbox arriving was never taken back, so a
   * player who was once sent one kept the staff queue panel for good: the role
   * stands, and losing it drops the queue already held.
   */
  private onRole(k: Record<string, any>): void {
    this.staff = !!k.staff;
    this.staffKnown = true;
    if ("duty" in k) this.duty = k.duty !== false;
    this.useAccount(typeof k.account === "number" ? k.account : null);
    if (!this.staff) {
      this.rows = [];
      this.ticket = null;
      this.history = [];
      this.viewingId = null;
      this.assistTab = "mine";
    } else if (!this.lean) {
      // The first hello can fail when the account signs in after the socket opens.
      void this.hello();
    }
  }

  /** Keep what is held per account under the account that is signed in. */
  private useAccount(account: number | null): void {
    if (account == null || account === this.accountId) return;
    this.accountId = account;
    this.queueSeen = loadQueueSeen(account);
    // The tab badge counts the player's unread replies, so the list is loaded
    // as soon as the account is known and not when the panel is first opened.
    void this.loadMine(false, "");
  }

  /** Show a pushed thread in the view the server built it for, or by the role when it names none. */
  private showThread(t: any): void {
    const view = t.view ?? (this.staff ? "staff" : "owner");
    if (view === "staff" && this.staff) {
      this.ticket = t;
      this.assistTab = "queue";
    } else if (view === "owner") {
      this.myTicket = t;
      this.assistTab = "mine";
    }
  }

  handleOob(event: string, _args: any[], kwargs: Record<string, any>): void {
    switch (event) {
      case "ticket_role":
        this.onRole(kwargs);
        break;
      case "ticket_inbox":
        // Before the server has stated the role, an inbox is the only sign of
        // staff. After, the role stands: a stray inbox must not hand a player
        // the staff queue.
        if (!this.staffKnown) this.staff = true;
        this.setRows(kwargs.tickets ?? [], true);
        break;
      case "ticket_upsert":
        this.onUpsert(kwargs);
        break;
      case "ticket_remove":
        this.rows = remove(this.rows, String(kwargs.id ?? ""));
        break;
      case "ticket_presence":
        this.rows = withPresence(this.rows, kwargs as PresencePatch);
        if (this.ticket && this.ticket.id === kwargs.id) {
          this.ticket = {
            ...this.ticket,
            presence: kwargs.presence ?? this.ticket.presence,
            seen: kwargs.seen ?? this.ticket.seen,
            account_online: kwargs.account_online ?? this.ticket.account_online,
          };
        }
        break;
      case "ticket_thread":
        if (!kwargs || !kwargs.id) break;
        this.showThread(kwargs);
        break;
      case "ticket_compose":
        this.composeRequest(kwargs?.kind);
        break;
      case "ticket_alert":
        this.onAlert(kwargs);
        break;
      case "ticket_unread":
        this.onUnread(kwargs);
        break;
      case "ticket_msg":
        this.onMessage(kwargs);
        break;
      default:
        break;
    }
  }

  // -- the queue ------------------------------------------------------------

  private setRows(next: TicketRow[], announce: boolean): void {
    const sorted = [...next].sort(byServerOrder);
    if (announce && this.loaded) {
      // The whole-queue protocol has no "new" event, so a row that was not
      // here before is the news. The first queue is the baseline, not news.
      const before = new Set(this.rows.map((t) => t.id));
      for (const t of sorted) {
        if (!before.has(t.id) && t.status === "pending") this.toastNew(t, "new");
      }
    }
    this.rows = sorted;
    this.loaded = true;
    this.refreshOpen(sorted);
    if (this.queueActive) this.markQueueSeen();
  }

  /** Keep an open ticket's header fresh when its row changes. */
  private refreshOpen(rows: TicketRow[]): void {
    if (!this.ticket) return;
    const row = rows.find((t) => t.id === this.ticket.id);
    if (row) this.ticket = { ...this.ticket, ...row, messages: this.ticket.messages, facts: this.ticket.facts };
  }

  private onUpsert(k: Record<string, any>): void {
    const row = k.ticket as TicketRow | undefined;
    if (!row?.id) return;
    this.rows = upsert(this.rows, row);
    this.loaded = true;
    this.refreshOpen([row]);
    if (k.notify && (k.why === "new" || k.why === "reopened")) this.toastNew(row, k.why);
    if (this.queueActive) this.markQueueSeen();
  }

  private toastNew(row: TicketRow, why: string): void {
    const label = String(row.label || "ticket").toLowerCase();
    const title = why === "reopened" ? `Reply on ${row.ref}` : `New ${label} ${row.ref}`;
    const nowSeconds = Math.floor(Date.now() / 1000);
    const body =
      why === "reopened"
        ? `${whoIs(row)} wrote back`
        : `${row.title || row.label} (${presenceText(row, nowSeconds)})`;
    toasts.push("ticket", title, body, 12000, true, () => {
      void this.openStaff(row.id);
      this.showTab("queue");
    });
    notify.ping(title, body, false);
  }

  private onAlert(k: Record<string, any>): void {
    const mins = Number(k.age_mins ?? 0);
    const ref = String(k.ref || "A ticket");
    const title = k.level === "warn" ? `${ref} has waited too long` : `${ref} is still unanswered`;
    const body = `${k.title || k.label || ""}${k.who ? ` (${k.who})` : ""}, ${mins} minutes, ${
      k.held ? "someone has it" : "nobody has it"
    }`;
    toasts.push("ticket", title, body, 12000, true, () => {
      if (k.id) void this.openStaff(String(k.id));
      this.showTab("queue");
    });
    notify.ping(title, body);
  }

  private onUnread(k: Record<string, any>): void {
    const mine = Number(k.mine ?? 0);
    if (mine > 0) {
      const title = `${mine} ${mine === 1 ? "request has" : "requests have"} a reply you have not read`;
      toasts.push("ticket", title, "Open My requests to read it.", 12000, true, () => this.showTab("mine"));
    }
    const open = Number(k.unanswered ?? 0);
    if (open > 0 && this.staff) {
      const nobody = Number(k.unclaimed ?? 0);
      toasts.push(
        "ticket",
        `${open} ${open === 1 ? "ticket is" : "tickets are"} unanswered`,
        nobody ? `${nobody} nobody has picked up.` : "",
        12000,
        true,
        () => this.showTab("queue"),
      );
    }
  }

  private onMessage(k: Record<string, any>): void {
    const appendTo = (t: any) => ({
      ...t,
      status: k.status ?? t.status,
      messages: [
        ...(t.messages ?? []),
        {
          origin: k.origin,
          text: k.text,
          html: k.html,
          sender: k.sender,
          sender_html: k.sender_html,
          senderHtml: k.sender_html,
          platform: k.platform,
          visibility: k.visibility,
          ts: k.ts,
        },
      ],
    });
    // Live append to the open detail of the same view only: a line meant for
    // the owner must not land in a staff view of the same ticket, and a note
    // meant for staff must never land in the owner's.
    const ownerLine = k.audience === "owner";
    const openStaff = !ownerLine && !!this.ticket && k.id === this.ticket.id;
    const openMine = ownerLine && !!this.myTicket && k.id === this.myTicket.id;
    if (openStaff) this.ticket = appendTo(this.ticket);
    if (openMine) this.myTicket = appendTo(this.myTicket);
    // Lists show the new state at once: a closed ticket used to read "pending"
    // until the list was reloaded.
    const touch = (rows: TicketRow[], unread: boolean) =>
      rows.map((t) =>
        t.id === k.id
          ? {
              ...t,
              status: k.status ?? t.status,
              updated: k.ts ?? t.updated,
              preview: k.visibility === "internal" ? t.preview : String(k.text ?? t.preview).slice(0, 120),
              unread: unread ? true : t.unread,
            }
          : t,
      );
    // News to the owner: a staff reply, or a closed, decided or reopened notice.
    // Picking a ticket up is in the thread but does not make it unread.
    const ours = k.audience === "owner" && !!k.origin && k.origin !== "player" && k.news !== false;
    if (ownerLine && this.myRows.some((t) => t.id === k.id)) this.myRows = touch(this.myRows, ours && !openMine);
    if (!ownerLine && this.rows.some((t) => t.id === k.id)) this.rows = touch(this.rows, false);
    if (this.queueActive) this.markQueueSeen();
    // Refresh the list's state and preview for the owner's own requests.
    if (ownerLine) this.myRev += 1;
    this.announce(k, openStaff, openMine);
  }

  /**
   * Say so when a ticket message is for this player and they are not already
   * looking at it. A staff reply used to reach a web player only as an update
   * to the list, so with the panel closed it arrived in silence.
   */
  private announce(k: Record<string, any>, openStaff: boolean, openMine: boolean): void {
    const ref = String(k.ref ?? `#${k.short_id ?? String(k.id ?? "").slice(0, 8)}`);
    const about = k.title || k.subject ? `${ref} ${k.title || k.subject}` : `${k.label ?? "Request"} ${ref}`;
    const preview = String(k.text ?? "").slice(0, 140);
    if (k.audience === "owner" && k.origin && k.origin !== "player" && k.news !== false && !openMine) {
      const title = k.origin === "system" ? `Update on ${about}` : `Staff replied: ${about}`;
      const body = k.origin === "system" ? preview : `${k.sender ?? "Staff"}: ${preview}`;
      toasts.push("ticket", title, body, 12000, true, () => {
        void this.openMine(String(k.id));
        this.showTab("mine");
      });
      notify.ping(title, body, false);
    } else if (k.audience === "assignee" && k.origin === "player" && !openStaff) {
      const title = `${whoIs({ requester_name: k.sender, account_name: "" })} replied: ${about}`;
      toasts.push("ticket", title, preview, 12000, true, () => {
        void this.openStaff(String(k.id));
        this.showTab("queue");
      });
      notify.ping(title, preview, false);
    }
  }

  // -- staff actions --------------------------------------------------------

  /** Open a ticket in the workbench. The thread comes back as the request's answer. */
  async openStaff(id: string): Promise<boolean> {
    this.viewingId = id;
    try {
      const t = await connection.request<any>("tickets", "ticket_get", { id });
      // A later request, a Back, or the loss of the role makes this answer stale.
      if (this.viewingId !== id || !this.staff) return false;
      this.showThread({ view: "staff", ...t });
      this.error = "";
      return true;
    } catch (e: any) {
      if (this.viewingId === id) this.error = String(e?.message || "Could not open that ticket.");
      return false;
    }
  }

  closeStaff(): void {
    this.viewingId = null;
    this.ticket = null;
  }

  /** One staff action. Resolves to the message to show; the open ticket is refreshed. */
  async act(id: string, action: string, extra: Record<string, unknown> = {}): Promise<TicketResult> {
    const epoch = this.epoch;
    try {
      const r = await connection.request<any>("tickets", "ticket_act", { id, action, ...extra });
      const result = { ok: true, message: String(r?.message ?? "Done."), puppet: r?.puppet ?? null };
      // An answer that lands after the account left is not shown to the next one.
      if (epoch !== this.epoch) return result;
      if (r?.ticket && this.staff && (!this.ticket || this.ticket.id === r.ticket.id)) this.ticket = r.ticket;
      // Done, and the ticket is now one this viewer may not read (a finished
      // ticket without the capability for the record): stop showing it.
      if (r && "ticket" in r && !r.ticket && id && this.ticket?.id === id) this.ticket = null;
      if (typeof r?.duty === "boolean") this.duty = r.duty;
      return result;
    } catch (e: any) {
      return fail(e);
    }
  }

  reply(id: string, text: string, internal = false): Promise<TicketResult> {
    return this.act(id, "reply", { text, internal });
  }
  claim(id: string, take = false): Promise<TicketResult> {
    return this.act(id, "claim", { take });
  }
  unclaim(id: string): Promise<TicketResult> {
    return this.act(id, "unclaim");
  }
  assign(id: string, to: string): Promise<TicketResult> {
    return this.act(id, "assign", { to });
  }
  setPriority(id: string, value: number): Promise<TicketResult> {
    return this.act(id, "priority", { value });
  }
  close(id: string): Promise<TicketResult> {
    return this.act(id, "close");
  }
  reopen(id: string): Promise<TicketResult> {
    return this.act(id, "reopen");
  }
  approve(id: string, reason = ""): Promise<TicketResult> {
    return this.act(id, "approve", { text: reason });
  }
  deny(id: string, reason = ""): Promise<TicketResult> {
    return this.act(id, "deny", { text: reason });
  }
  merge(id: string, into: string): Promise<TicketResult> {
    return this.act(id, "merge", { into });
  }
  /** Claim a puppet request and, when the server moved us to the NPC, puppet it. */
  async puppet(id: string, take = false): Promise<TicketResult> {
    const result = await this.act(id, "puppet", { take });
    if (result.ok && result.puppet?.ok && result.puppet.command) connection.sendCommand(result.puppet.command);
    return result;
  }
  /** Run the command the server gave for entering an NPC, when the move could not be done for us. */
  enterNpc(command: string): void {
    if (command) connection.sendCommand(command);
  }
  async setDuty(on: boolean): Promise<TicketResult> {
    return this.act("", "duty", { on });
  }

  async loadHistory(search = "", offset = 0): Promise<void> {
    const epoch = this.epoch;
    try {
      const r = await connection.request<any>("tickets", "ticket_list", {
        history: true,
        search,
        offset,
      });
      if (epoch !== this.epoch) return;
      const rows: TicketRow[] = r?.tickets ?? [];
      this.history = offset ? [...this.history, ...rows] : rows;
      this.historyMore = !!r?.has_next;
      this.historyCapped = !!r?.capped;
      this.error = "";
    } catch (e: any) {
      if (!offset) this.history = [];
      this.error = String(e?.message || "Could not load the record.");
    }
  }

  async loadBugDetail(id: string): Promise<any> {
    try {
      return await connection.request<any>("tickets", "ticket_bug_detail", { id });
    } catch {
      return null;
    }
  }

  async loadReplies(): Promise<void> {
    const epoch = this.epoch;
    try {
      const r = await connection.request<any>("tickets", "ticket_replies", { op: "list" });
      if (epoch !== this.epoch) return;
      this.replies = r?.replies ?? [];
    } catch {
      this.replies = [];
    }
  }

  /** The text of a saved reply, filled in for a ticket, to show before it is sent. */
  async expandReply(id: string, name: string): Promise<string | null> {
    try {
      const r = await connection.request<any>("tickets", "ticket_replies", { op: "expand", id, name });
      return String(r?.text ?? "");
    } catch {
      return null;
    }
  }

  async saveReply(name: string, body: string, shared = false): Promise<TicketResult> {
    try {
      const r = await connection.request<any>("tickets", "ticket_replies", { op: "save", name, body, shared });
      this.replies = r?.replies ?? this.replies;
      return { ok: true, message: "Saved." };
    } catch (e: any) {
      return fail(e);
    }
  }

  async deleteReply(name: string): Promise<TicketResult> {
    try {
      const r = await connection.request<any>("tickets", "ticket_replies", { op: "delete", name });
      this.replies = r?.replies ?? this.replies;
      return { ok: true, message: "Deleted." };
    } catch (e: any) {
      return fail(e);
    }
  }

  markQueueSeen(): void {
    let changed = false;
    const next: Record<string, number> = {};
    for (const t of this.rows) {
      const seen = this.queueSeen[t.id] ?? 0;
      const fresh = t.updated ?? 0;
      next[t.id] = Math.max(seen, fresh);
      if (next[t.id] !== seen) changed = true;
    }
    // Rows only count while open; forget ids that left the queue with them.
    for (const id of Object.keys(this.queueSeen)) {
      if (!(id in next)) changed = true;
    }
    if (!changed) return;
    this.queueSeen = next;
    saveQueueSeen(this.accountId, next);
  }

  // -- a player's own requests ---------------------------------------------

  /**
   * Load the player's own requests. A failure is kept and shown: it used to
   * blank the list, which read as "you have no requests". The panel calls this
   * once the socket is open; a request made before that always failed. A search
   * covers finished requests too, and never a staff-only note.
   */
  async loadMine(includeClosed = false, search = this.mySearch): Promise<void> {
    const epoch = this.epoch;
    try {
      const r = await connection.request<any>("tickets", "my_tickets", { closed: includeClosed, search });
      if (epoch !== this.epoch) return;
      this.myRows = r?.tickets ?? [];
      this.myError = "";
    } catch (e: any) {
      if (epoch === this.epoch) this.myError = String(e?.message || "Could not load your requests.");
    }
  }

  /** Open one request. Reading it is the server's mark, so another device agrees. */
  async openMine(id: string): Promise<void> {
    const epoch = this.epoch;
    try {
      const t = await connection.request<any>("tickets", "my_ticket", { id });
      if (epoch !== this.epoch) return;
      this.myTicket = t;
      this.myError = "";
      this.assistTab = "mine";
      this.myRows = this.myRows.map((r) => (r.id === id ? { ...r, unread: false } : r));
    } catch (e: any) {
      if (epoch === this.epoch) this.myError = String(e?.message || "Could not open that request.");
    }
  }

  closeMine(): void {
    this.myTicket = null;
    this.myRev += 1;
  }

  /** Reply to, or withdraw, one of the player's own requests. */
  async mineAct(id: string, action: "reply" | "withdraw", text = ""): Promise<TicketResult> {
    const epoch = this.epoch;
    try {
      const r = await connection.request<any>("tickets", "my_ticket_act", { id, action, text });
      if (epoch !== this.epoch) return { ok: true, message: String(r?.message ?? "Done.") };
      if (r?.ticket) {
        this.myTicket = r.ticket;
        this.myRows = this.myRows.map((t) => (t.id === id ? { ...t, unread: false } : t));
      }
      this.myRev += 1;
      return { ok: true, message: String(r?.message ?? "Done.") };
    } catch (e: any) {
      return fail(e);
    }
  }

  /** File a new request of any kind a player may open. */
  async file(request: NewRequest): Promise<TicketResult> {
    const epoch = this.epoch;
    try {
      const r = await connection.request<any>("tickets", "ticket_open", request);
      if (epoch !== this.epoch) return { ok: true, message: String(r?.message ?? "Sent.") };
      if (r?.ticket) {
        this.myTicket = r.ticket;
        this.assistTab = "mine";
      }
      this.myRev += 1;
      return { ok: true, message: String(r?.message ?? "Sent.") };
    } catch (e: any) {
      return fail(e, "The request could not be sent.");
    }
  }

  /** The words of the new-request form, from the game. A game that does not know the request keeps the defaults. */
  async loadForm(): Promise<void> {
    if (this.formLoaded) return;
    try {
      this.form = mergeForm(await connection.request<any>("tickets", "ticket_form"));
      this.formLoaded = true;
    } catch {
      /* the defaults stand */
    }
  }

  /** Help pages that may answer the question before it goes to staff. */
  async suggest(text: string): Promise<{ key: string; summary: string }[]> {
    if (text.trim().length < 4) return [];
    try {
      const r = await connection.request<any>("tickets", "ticket_suggest", { text });
      return r?.topics ?? [];
    } catch {
      return [];
    }
  }
}

export const tickets = new Tickets();
