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

export interface ChatChannel {
  key: string;
  name: string;
  speakCmd?: string;
  color?: string;
  mandatory?: boolean;
}
export interface ChatMsg {
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
const TYPING_MS = 6000;
const PREFS_KEY = "underspire.channelprefs.v1";

function loadChannelPrefs(): Record<string, { color?: string; notify?: string }> {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
  } catch {
    return {};
  }
}

function toMsg(r: any): ChatMsg {
  return {
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
  staff = $state(false); // set true once the server sends assist_inbox (Builder+ only)
  active = $state<string>("");
  // Per-channel overrides: colour + notify mode ("all" | "mention" | "none").
  channelPrefs = $state<Record<string, { color?: string; notify?: string }>>(loadChannelPrefs());

  private typingTimers: Record<string, ReturnType<typeof setTimeout>> = {};

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
      case "ticket_inbox": {
        this.staff = true;
        const next = kwargs.tickets ?? [];
        if (this.tickets.length) {
          const prev = new Set(this.tickets.map((t: any) => t.id));
          for (const t of next) {
            if (!prev.has(t.id)) {
              toasts.push("ticket", `New ${t.label || "ticket"}`, t.account_name || t.short_id);
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
        this.ticket = kwargs && kwargs.id ? kwargs : null;
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
        if (this.ticket && kwargs.id === this.ticket.id) this.ticket = appendTo(this.ticket);
        if (this.myTicket && kwargs.id === this.myTicket.id) this.myTicket = appendTo(this.myTicket);
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

  openTicket(id: string): void {
    connection.sendCommand(`@ticket ${id}`);
  }
  ticketReply(id: string, text: string, internal = false): void {
    const t = (text || "").trim();
    if (!t) return;
    connection.sendCommand(internal ? `@ticket/internal ${id} = ${t}` : `@ticket ${id} = ${t}`);
  }
  ticketClaim(id: string): void {
    connection.sendCommand(`@claim ${id}`);
  }
  ticketResolve(id: string): void {
    connection.sendCommand(`@resolve ${id}`);
  }
  ticketApprove(id: string, reason = ""): void {
    connection.sendCommand(reason ? `@approve ${id} = ${reason}` : `@approve ${id}`);
  }
  ticketDeny(id: string, reason = ""): void {
    connection.sendCommand(reason ? `@deny ${id} = ${reason}` : `@deny ${id}`);
  }
  async loadTicketHistory(): Promise<void> {
    try {
      const r = await connection.request<any>("tickets", "ticket_list", { history: true });
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

  async loadMyTickets(includeClosed = false): Promise<void> {
    try {
      const r = await connection.request<any>("tickets", "my_tickets", { closed: includeClosed });
      this.myTickets = r?.tickets ?? [];
    } catch {
      this.myTickets = [];
    }
  }
  async openMyTicket(id: string): Promise<void> {
    try {
      this.myTicket = await connection.request<any>("tickets", "my_ticket", { id });
    } catch {
      this.myTicket = null;
    }
  }
  replyMyTicket(id: string, text: string): void {
    const t = (text || "").trim();
    if (t) connection.sendCommand(`@ticket ${id} = ${t}`);
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
    toasts.push("mention", `@ ${name}`, `${kwargs.sender ?? ""}: ${kwargs.text ?? ""}`);
    playMention();
    // Title/desktop attention if the tab is in the background (no extra sound).
    notify.ping(`@ ${name}`, `${kwargs.sender ?? ""}: ${kwargs.text ?? ""}`, false);
  }

  private addMsg(p: any): void {
    const key = p.channel;
    if (!key) return;
    const m = toMsg(p);
    const prev = this.messages[key] ?? [];
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

  private setHistory(map: Record<string, any[]>): void {
    const msgs = { ...this.messages };
    for (const [key, rows] of Object.entries(map)) {
      if (Array.isArray(rows)) msgs[key] = rows.map(toMsg);
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
