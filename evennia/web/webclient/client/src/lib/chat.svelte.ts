// Chat manager data layer. Consumes the Azaban `oob` channel events (the game
// already sends these to any websocket session) into reactive state, and posts
// via the channel's speak command. The channel UI reads this store.
//
// Event shapes (from world/channels): channels_list → args = [{key,name,speak_cmd,
// color,mandatory}]; channel_msg → kwargs {channel,text,sender,platform,ts,msg_id};
// channel_history → kwargs {key: [{msg_id,text,sender,platform,ts}]}; channel_unread
// / channel_online → kwargs {key: count}; channel_topic → {channel_key,topic};
// channel_reaction → {channel_key,msg_id,emoji,sender_name,delta,count}; channel_typing
// → {channel_key,sender_name,platform}; assist_inbox → {threads:[…]}.

import { commands } from "./commands.svelte";
import { connection } from "./evennia.svelte";
import { toasts } from "./toasts.svelte";
import { notify } from "./notify.svelte";
import { playMention } from "./audio";
import { renderBody, renderSender } from "./markup";
import { settings } from "./settings.svelte";

export interface ChatChannel {
  key: string;
  name: string;
  speakCmd?: string;
  color?: string;
  mandatory?: boolean;
}
export interface ChatMsg {
  /** Client-side key, unique for the page's life. See toMsg. */
  uid: number;
  msgId: string;
  sender: string;
  senderHtml?: string;
  text: string;
  html?: string;
  ts: number;
  platform?: string;
  reactions: Record<string, number>;
}

const MAX_PER_CHANNEL = 500;
const SEEN_KEY = "underspire.tickets.seen.v1";

/** The answer to a ticket action, for the panel to show. */
export interface TicketResult {
  ok: boolean;
  message: string;
}

function loadSeen(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) || "{}");
  } catch {
    return {};
  }
}
const TYPING_MS = 6000;
const PREFS_KEY = "underspire.channelprefs.v1";

function loadChannelPrefs(): Record<string, { color?: string; notify?: string }> {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
  } catch {
    return {};
  }
}

// Rendering keys by uid, not msgId: a message without an id (or the same one
// delivered twice) gave the keyed list duplicate keys, which a production
// build does not report and can leave rendered wrong.
let nextUid = 1;

function toMsg(r: any): ChatMsg {
  return {
    uid: nextUid++,
    msgId: String(r.msg_id ?? r.msgId ?? ""),
    sender: r.sender ?? "",
    senderHtml: r.sender_html ?? r.senderHtml,
    text: r.text ?? "",
    html: r.html,
    ts: r.ts ? r.ts * 1000 : Date.now(),
    platform: r.platform,
    reactions: {},
  };
}

class Chat {
  channels = $state<ChatChannel[]>([]);
  messages = $state<Record<string, ChatMsg[]>>({});
  unread = $state<Record<string, number>>({});
  online = $state<Record<string, number>>({});
  topics = $state<Record<string, string>>({});
  typing = $state<Record<string, string[]>>({});
  pins = $state<Record<string, { msgId: string; text: string; by: string } | null>>({});
  muted = $state<Record<string, boolean>>({});
  readMark = $state<Record<string, number>>({}); // ts last seen per channel (for the "new" divider)
  mentions = $state<Record<string, boolean>>({}); // channel has an unread @-mention
  assistThreads = $state<any[]>([]);
  assistThread = $state<{ accountId: any; accountKey: string; messages: any[] } | null>(null);
  tickets = $state<any[]>([]); // unified ticket inbox (all kinds)
  ticket = $state<any | null>(null); // open ticket detail
  ticketHistory = $state<any[]>([]); // resolved/closed tickets (on demand)
  myTickets = $state<any[]>([]); // the player's own tickets
  myTicket = $state<any | null>(null); // player's open ticket detail
  /** Why the player's list could not load; shown instead of "no tickets". */
  myTicketsError = $state("");
  /** The player's current search in My Tickets ("" = none). */
  myTicketsSearch = $state("");
  /** Bumped when one of the player's tickets changes, so the list reloads. */
  myTicketsRev = $state(0);
  /** This session works the staff ticket queue (ticket_role, or an assist/ticket inbox arriving). */
  staff = $state(false);
  /**
   * The server has said whether this session is staff. Until then `staff`
   * false only means "not yet known": a layout restored at page load keeps
   * its queue panel until the answer arrives.
   */
  staffKnown = $state(false);
  private assistViewer = false;
  active = $state<string>("");
  // Per-channel overrides: colour + notify mode ("all" | "mention" | "none").
  channelPrefs = $state<Record<string, { color?: string; notify?: string }>>(loadChannelPrefs());

  private typingTimers: Record<string, ReturnType<typeof setTimeout>> = {};
  /** Opens a panel by view id. Set from main.ts: dock imports this store. */
  private openPanel: ((view: string) => void) | null = null;

  setPanelOpener(fn: (view: string) => void): void {
    this.openPanel = fn;
  }

  handleOob(event: string, args: any[], kwargs: Record<string, any>): void {
    switch (event) {
      case "channels_list":
        this.setChannels(Array.isArray(args) ? args : []);
        break;
      case "channel_msg":
        this.addMsg(kwargs);
        break;
      case "channel_history":
        this.setHistory(kwargs);
        break;
      case "channel_unread":
        this.unread = { ...this.unread, ...kwargs };
        break;
      case "channel_online":
        this.online = { ...(kwargs as Record<string, number>) };
        break;
      case "channel_topic":
        if (kwargs.channel_key)
          this.topics = { ...this.topics, [kwargs.channel_key]: kwargs.topic ?? "" };
        break;
      case "channel_reaction":
        this.applyReaction(kwargs);
        break;
      case "channel_msg_delete": {
        const key = kwargs.channel;
        const id = String(kwargs.msg_id ?? "");
        if (key && id && this.messages[key]) {
          this.messages = {
            ...this.messages,
            [key]: this.messages[key].filter((m) => m.msgId !== id),
          };
        }
        break;
      }
      case "channel_typing":
        this.addTyping(kwargs);
        break;
      case "channel_pin": {
        const key = kwargs.channel_key;
        if (!key) break;
        this.pins = {
          ...this.pins,
          [key]:
            kwargs.action === "clear"
              ? null
              : { msgId: String(kwargs.msg_id ?? ""), text: kwargs.text ?? "", by: kwargs.pinned_by ?? "" },
        };
        break;
      }
      case "assist_inbox": {
        this.staff = true;
        this.assistViewer = true;
        const next = kwargs.threads ?? [];
        // Toast newly-arrived tickets (not on the initial inbox push).
        if (this.assistThreads.length) {
          const prev = new Set(this.assistThreads.map((t: any) => t.account_id));
          for (const t of next) {
            if (!prev.has(t.account_id)) {
              toasts.push("assist", "New ticket", t.account_key || `#${t.account_id}`);
            }
          }
        }
        this.assistThreads = next;
        break;
      }
      case "assist_thread":
        this.assistThread = {
          accountId: kwargs.account_id,
          accountKey: kwargs.account_key ?? "",
          messages: kwargs.messages ?? [],
        };
        break;
      case "ticket_role":
        // The server's answer, on login and on every channel resync. Reading
        // staff from an inbox arriving was never taken back, so a player who
        // was once sent one kept the staff queue panel for good.
        this.staff = !!kwargs.staff || this.assistViewer;
        this.staffKnown = true;
        break;
      case "ticket_inbox": {
        // Before the server has stated the role, an inbox is the only sign of
        // staff. After, the role stands: a stray inbox must not hand a player
        // the staff queue.
        if (!this.staffKnown) this.staff = true;
        const next = kwargs.tickets ?? [];
        if (this.tickets.length) {
          const prev = new Set(this.tickets.map((t: any) => t.id));
          for (const t of next) {
            if (!prev.has(t.id)) {
              toasts.push(
                "ticket",
                `New ${t.label || "ticket"}`,
                t.requester_name || t.account_name || t.short_id,
              );
            }
          }
        }
        this.tickets = next;
        // Keep an open detail view fresh when its row changes.
        if (this.ticket) {
          const upd = next.find((t: any) => t.id === this.ticket.id);
          if (upd) this.ticket = { ...this.ticket, ...upd };
        }
        break;
      }
      case "ticket_thread":
        // Staff get the help-desk view; a player's own ticket opens in My
        // Tickets (the staff panel does not exist for them).
        if (!kwargs || !kwargs.id) break;
        if (this.staff) this.ticket = kwargs;
        else this.myTicket = kwargs;
        break;
      case "ticket_alert": {
        const title = `Unclaimed ${kwargs.label || "ticket"}`;
        const body = `${kwargs.who || ""} waiting ${kwargs.age_mins ?? "?"}m`;
        toasts.push("ticket", title, body);
        notify.ping(title, body);
        break;
      }
      case "ticket_msg": {
        const appendTo = (t: any) => ({
          ...t,
          status: kwargs.status ?? t.status,
          messages: [
            ...(t.messages ?? []),
            {
              origin: kwargs.origin,
              text: kwargs.text,
              html: kwargs.html,
              sender: kwargs.sender,
              sender_html: kwargs.sender_html,
              senderHtml: kwargs.sender_html,
              platform: kwargs.platform,
              visibility: kwargs.visibility,
              ts: kwargs.ts,
            },
          ],
        });
        // Live append to whichever open detail matches (staff or player view).
        const openStaff = !!this.ticket && kwargs.id === this.ticket.id;
        const openMine = !!this.myTicket && kwargs.id === this.myTicket.id;
        if (openStaff) this.ticket = appendTo(this.ticket);
        if (openMine) this.myTicket = appendTo(this.myTicket);
        // Lists show the new status at once: a closed ticket used to read
        // "pending" until the list was reloaded.
        const restatus = (rows: any[]) =>
          rows.map((t: any) =>
            t.id === kwargs.id ? { ...t, status: kwargs.status ?? t.status, updated: kwargs.ts ?? t.updated } : t,
          );
        if (this.myTickets.some((t: any) => t.id === kwargs.id)) this.myTickets = restatus(this.myTickets);
        if (this.tickets.some((t: any) => t.id === kwargs.id)) this.tickets = restatus(this.tickets);
        // Refresh the list's status and preview for the owner's own tickets.
        if (!this.staff || this.myTickets.some((t: any) => t.id === kwargs.id)) this.myTicketsRev += 1;
        this.announceTicket(kwargs, openStaff, openMine);
        break;
      }
      default:
        break; // channel_pin / community_* handled in later passes
    }
  }

  // -- UI actions --------------------------------------------------------

  setActive(key: string): void {
    // Mark the channel we're leaving as read-up-to its last message, so the
    // "new" divider lands correctly when we come back.
    if (this.active && this.active !== key) {
      const arr = this.messages[this.active];
      this.readMark = {
        ...this.readMark,
        [this.active]: arr && arr.length ? arr[arr.length - 1].ts : Date.now(),
      };
    }
    this.active = key;
    this.clearUnread(key);
    if (this.mentions[key]) {
      const m = { ...this.mentions };
      delete m[key];
      this.mentions = m;
    }
  }

  clearUnread(key: string): void {
    if (this.unread[key]) {
      const u = { ...this.unread };
      delete u[key];
      this.unread = u;
    }
    // Tell the server to clear its unread cache for this tab.
    connection.sendCommand(`@clear_unread ${key}`);
  }

  /** Ask the server to (re)push the channel list, comms status, assist inbox. */
  syncChannels(): void {
    connection.sendCommand("@sync_channels");
  }

  post(key: string, text: string): void {
    const t = (text || "").trim();
    if (!t) return;
    const ch = this.channels.find((c) => c.key === key);
    // Posting is a real command → goes through history.
    commands.run(ch?.speakCmd ? `${ch.speakCmd} ${t}` : `${key} ${t}`);
  }

  /** Toggle a reaction on a message (server echoes a channel_reaction). */
  react(msgId: string, emoji: string): void {
    if (msgId && emoji) connection.sendCommand(`@chreact ${msgId} ${emoji}`);
  }

  pin(key: string, text: string): void {
    const t = (text || "").trim();
    if (t) connection.sendCommand(`@chpin ${key} = ${t}`);
  }
  unpin(key: string): void {
    connection.sendCommand(`@chpin/clear ${key}`);
  }
  toggleMute(key: string): void {
    const m = !this.muted[key];
    this.muted = { ...this.muted, [key]: m };
    connection.sendCommand(m ? `@chmute ${key}` : `@chmute/unmute ${key}`);
  }

  channelColor(key: string): string | undefined {
    return this.channelPrefs[key]?.color;
  }
  channelNotify(key: string): string {
    return this.channelPrefs[key]?.notify ?? "mention";
  }
  setChannelColor(key: string, color: string): void {
    this.channelPrefs = { ...this.channelPrefs, [key]: { ...this.channelPrefs[key], color } };
    this.saveChannelPrefs();
  }
  setChannelNotify(key: string, notify: string): void {
    this.channelPrefs = { ...this.channelPrefs, [key]: { ...this.channelPrefs[key], notify } };
    this.saveChannelPrefs();
  }
  private saveChannelPrefs(): void {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(this.channelPrefs));
    } catch {
      /* ignore */
    }
  }

  // -- assist help-desk (staff) -----------------------------------------

  openAssistThread(accountId: any): void {
    connection.sendCommand(`@assistview #${accountId}`);
  }

  assistReply(accountKeyOrId: any, text: string): void {
    const t = (text || "").trim();
    if (t) connection.sendCommand(`xassistreply ${accountKeyOrId} ${t}`);
  }

  assistClaim(accountId: any): void {
    connection.sendCommand(`@assistclaim #${accountId}`);
  }

  assistStatus(accountId: any, status: string): void {
    connection.sendCommand(`@assiststatus #${accountId} ${status}`);
  }

  // -- unified tickets (staff) ------------------------------------------
  //
  // Panel actions go through the ticket_act RPC. They used to type the
  // matching command, and each command printed its confirmation ("Posted.",
  // "Ticket #... closed.") into the terminal beside the panel. The answer now
  // comes back to the panel, with the ticket as it stands.

  openTicket(id: string): void {
    connection.sendCommand(`@ticket ${id}`);
  }
  /** One staff action. Resolves to the message to show; the open ticket is refreshed. */
  async ticketAct(id: string, action: string, extra: Record<string, unknown> = {}): Promise<TicketResult> {
    try {
      const r = await connection.request<any>("tickets", "ticket_act", { id, action, ...extra });
      if (r?.ticket && (!this.ticket || this.ticket.id === r.ticket.id)) this.ticket = r.ticket;
      return { ok: true, message: String(r?.message ?? "Done.") };
    } catch (e: any) {
      return { ok: false, message: e?.message || "That did not go through." };
    }
  }
  ticketReply(id: string, text: string, internal = false): Promise<TicketResult> {
    return this.ticketAct(id, "reply", { text, internal });
  }
  ticketClaim(id: string): Promise<TicketResult> {
    return this.ticketAct(id, "claim");
  }
  ticketResolve(id: string): Promise<TicketResult> {
    return this.ticketAct(id, "close");
  }
  ticketReopen(id: string): Promise<TicketResult> {
    return this.ticketAct(id, "reopen");
  }
  ticketApprove(id: string, reason = ""): Promise<TicketResult> {
    return this.ticketAct(id, "approve", { text: reason });
  }
  ticketDeny(id: string, reason = ""): Promise<TicketResult> {
    return this.ticketAct(id, "deny", { text: reason });
  }
  async loadTicketHistory(search = ""): Promise<void> {
    try {
      const r = await connection.request<any>("tickets", "ticket_list", { history: true, search });
      this.ticketHistory = r?.tickets ?? [];
    } catch {
      this.ticketHistory = [];
    }
  }
  async loadBugDetail(id: string): Promise<any> {
    try {
      return await connection.request<any>("tickets", "ticket_bug_detail", { id });
    } catch {
      return null;
    }
  }

  // -- player's own tickets ---------------------------------------------

  /**
   * Load the player's own tickets. A failure is kept and shown: it used to
   * blank the list, which read as "you have no tickets". The panel calls this
   * once the socket is open; a request made before that always failed.
   * A search covers finished tickets too, and never a staff-only note.
   */
  async loadMyTickets(includeClosed = false, search = this.myTicketsSearch): Promise<void> {
    try {
      const r = await connection.request<any>("tickets", "my_tickets", { closed: includeClosed, search });
      this.myTickets = r?.tickets ?? [];
      this.myTicketsError = "";
    } catch (e: any) {
      this.myTicketsError = e?.message || "Could not load tickets.";
    }
  }
  async openMyTicket(id: string): Promise<void> {
    try {
      this.myTicket = await connection.request<any>("tickets", "my_ticket", { id });
      this.myTicketsError = "";
      this.markSeen(this.myTicket);
    } catch (e: any) {
      this.myTicketsError = e?.message || "Could not open that ticket.";
    }
  }
  /** Reply to, or withdraw, one of the player's own tickets. */
  async myTicketAct(id: string, action: "reply" | "withdraw", text = ""): Promise<TicketResult> {
    try {
      const r = await connection.request<any>("tickets", "my_ticket_act", { id, action, text });
      if (r?.ticket) {
        this.myTicket = r.ticket;
        this.markSeen(r.ticket);
      }
      this.myTicketsRev += 1;
      return { ok: true, message: String(r?.message ?? "Done.") };
    } catch (e: any) {
      return { ok: false, message: e?.message || "That did not go through." };
    }
  }
  replyMyTicket(id: string, text: string): Promise<TicketResult> {
    return this.myTicketAct(id, "reply", text);
  }
  /** File a new help request without leaving the panel. */
  async openRequest(subject: string, text: string): Promise<TicketResult> {
    try {
      const r = await connection.request<any>("tickets", "my_ticket_open", { subject, text });
      if (r?.ticket) this.myTicket = r.ticket;
      this.myTicketsRev += 1;
      return { ok: true, message: String(r?.message ?? "Filed.") };
    } catch (e: any) {
      return { ok: false, message: e?.message || "The request could not be filed." };
    }
  }

  // "Last seen" per ticket, so My Tickets can mark a reply the player has not
  // read. Per browser, like the rest of the panel's conveniences.
  seen = $state<Record<string, number>>(loadSeen());
  private markSeen(t: any): void {
    if (!t?.id) return;
    const last = Math.max(t.updated ?? 0, ...(t.messages ?? []).map((m: any) => m.ts ?? 0));
    this.seen = { ...this.seen, [t.id]: last };
    try {
      localStorage.setItem(SEEN_KEY, JSON.stringify(this.seen));
    } catch {
      /* ignore */
    }
  }
  /** Whether staff (or the system) moved a ticket since the player last opened it. */
  unseen(t: any): boolean {
    return !!t?.id && (t.updated ?? 0) > (this.seen[t.id] ?? 0) && t.status !== "pending";
  }

  /**
   * Say so when a ticket message is for this player and they are not already
   * looking at it. A staff reply used to reach a web player only as an update
   * to My Tickets, so with the panel closed it arrived in silence.
   */
  private announceTicket(k: Record<string, any>, openStaff: boolean, openMine: boolean): void {
    const ref = `#${k.short_id ?? String(k.id ?? "").slice(0, 8)}`;
    const about = k.subject ? `${ref} ${k.subject}` : `${k.label ?? "Ticket"} ${ref}`;
    const preview = String(k.text ?? "").slice(0, 140);
    if (k.audience === "owner" && k.origin && k.origin !== "player" && !openMine) {
      const title = k.origin === "system" ? `Ticket update: ${about}` : `Staff replied: ${about}`;
      const body = k.origin === "system" ? preview : `${k.sender ?? "Staff"}: ${preview}`;
      toasts.push("ticket", title, body, 12000, true, () => {
        void this.openMyTicket(String(k.id));
        this.openPanel?.("mytickets");
      });
      notify.ping(title, body, false);
    } else if (k.audience === "assignee" && k.origin === "player" && !openStaff) {
      const title = `Player replied: ${about}`;
      toasts.push("ticket", title, `${k.sender ?? ""}: ${preview}`, 12000, true, () => {
        this.openTicket(String(k.id));
        this.openPanel?.("tickets");
      });
      notify.ping(title, preview, false);
    }
  }

  // -- event handlers ----------------------------------------------------

  private setChannels(list: any[]): void {
    this.channels = list.map((c) => ({
      key: c.key,
      name: c.name ?? c.key,
      speakCmd: c.speak_cmd ?? c.speakCmd,
      color: c.color,
      mandatory: c.mandatory,
    }));
    // Seed authoritative mute + pin state from the payload.
    const msgs = { ...this.messages };
    const muted = { ...this.muted };
    const pins = { ...this.pins };
    for (const c of list) {
      if (!msgs[c.key]) msgs[c.key] = [];
      if ("muted" in c) muted[c.key] = !!c.muted;
      if ("pin" in c) {
        pins[c.key] = c.pin
          ? { msgId: String(c.pin.msg_id ?? ""), text: c.pin.text ?? "", by: c.pin.pinned_by ?? "" }
          : null;
      }
    }
    this.messages = msgs;
    this.muted = muted;
    this.pins = pins;
    if (!this.active && this.channels.length) this.active = this.channels[0].key;
  }

  /** A @-mention ping for this session (server-detected): highlight + toast + sound. */
  onMention(kwargs: any): void {
    const key = kwargs.channel;
    if (!key) return;
    if (this.channelNotify(key) === "none") return; // channel muted for alerts
    this.mentions = { ...this.mentions, [key]: true };
    if (key !== this.active) {
      this.unread = { ...this.unread, [key]: (this.unread[key] ?? 0) + 1 };
    }
    const name = this.channels.find((c) => c.key === key)?.name ?? key;
    // With channel echo on, the message itself is spoken from the terminal.
    toasts.push("mention", `@ ${name}`, `${kwargs.sender ?? ""}: ${kwargs.text ?? ""}`, undefined, !settings.channelEcho);
    playMention();
    // Title/desktop attention if the tab is in the background (no extra sound).
    notify.ping(`@ ${name}`, `${kwargs.sender ?? ""}: ${kwargs.text ?? ""}`, false);
  }

  private addMsg(p: any): void {
    const key = p.channel;
    if (!key) return;
    const m = toMsg(p);
    const prev = this.messages[key] ?? [];
    // The same message twice (a resync racing a live send) is one message.
    if (m.msgId && prev.some((x) => x.msgId === m.msgId)) return;
    const arr = [...prev, m];
    if (arr.length > MAX_PER_CHANNEL) arr.splice(0, arr.length - MAX_PER_CHANNEL);
    this.messages = { ...this.messages, [key]: arr };
    if (key !== this.active) {
      this.unread = { ...this.unread, [key]: (this.unread[key] ?? 0) + 1 };
      // "all" mode: ping on every message in this channel while unfocused.
      if (this.channelNotify(key) === "all") {
        const name = this.channels.find((c) => c.key === key)?.name ?? key;
        notify.ping(name, `${m.sender}: ${m.text}`);
      }
    }
    this.dropTyping(key, m.sender);
  }

  /**
   * Merge a history push into what the page already holds. It used to replace
   * the channel's list outright, so a history payload (a resync, or the
   * re-push after a deletion) wiped every message received since the page
   * loaded that the backlog query did not return.
   */
  private setHistory(map: Record<string, any[]>): void {
    const msgs = { ...this.messages };
    for (const [key, rows] of Object.entries(map)) {
      if (!Array.isArray(rows)) continue;
      const incoming = rows.map(toMsg);
      const known = new Set(incoming.map((m) => m.msgId).filter(Boolean));
      const live = (msgs[key] ?? []).filter((m) => !m.msgId || !known.has(m.msgId));
      const merged = [...incoming, ...live].sort((a, b) => a.ts - b.ts);
      if (merged.length > MAX_PER_CHANNEL) merged.splice(0, merged.length - MAX_PER_CHANNEL);
      msgs[key] = merged;
    }
    this.messages = msgs;
  }

  private applyReaction(p: any): void {
    const key = p.channel_key ?? p.channel;
    const emoji = p.emoji ?? p.reaction;
    const id = String(p.msg_id ?? "");
    if (!key || !emoji || !id) return;
    const arr = this.messages[key];
    if (!arr) return;
    this.messages = {
      ...this.messages,
      [key]: arr.map((m) =>
        m.msgId === id
          ? { ...m, reactions: { ...m.reactions, [emoji]: p.count ?? (m.reactions[emoji] ?? 0) + (p.delta ?? 1) } }
          : m,
      ),
    };
  }

  private addTyping(p: any): void {
    const key = p.channel_key ?? p.channel;
    const who = p.sender_name ?? p.sender;
    if (!key || !who) return;
    const list = this.typing[key] ?? [];
    if (!list.includes(who)) this.typing = { ...this.typing, [key]: [...list, who] };
    const tk = `${key}::${who}`;
    clearTimeout(this.typingTimers[tk]);
    this.typingTimers[tk] = setTimeout(() => this.dropTyping(key, who), TYPING_MS);
  }

  private dropTyping(key: string, who: string): void {
    const list = this.typing[key];
    if (list?.includes(who)) {
      this.typing = { ...this.typing, [key]: list.filter((n) => n !== who) };
    }
  }
}

export const chat = new Chat();
