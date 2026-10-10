// Chat manager data layer. Consumes the Azaban `oob` channel events (the game
// already sends these to any websocket session) into reactive state, and posts
// via the channel's speak command. The channel UI reads this store.
//
// Event shapes (from world/channels): channels_list → args = [{key,name,speak_cmd,
// color,mandatory}]; channel_msg → kwargs {channel,text,sender,platform,ts,msg_id};
// channel_history → kwargs {key: [{msg_id,text,sender,platform,ts}]}; channel_unread
// / channel_online → kwargs {key: count}; channel_topic → {channel_key,topic};
// channel_reaction → {channel_key,msg_id,emoji,sender_name,delta,count}; channel_typing
// → {channel_key,sender_name,platform}. Ticket events (ticket_*) belong to the
// tickets store (tickets.svelte.ts), which feeds the Assist panel.

import { commands } from "./commands.svelte";
import { connection } from "./evennia.svelte";
import { toasts } from "./toasts.svelte";
import { notify } from "./notify.svelte";
import { playMention } from "./audio";
import { renderBody, renderSender } from "./markup";
import { settings } from "./settings.svelte";
import { tickets } from "./tickets.svelte";
import { triggers } from "./triggers.svelte";

// The keys the tickets store keeps per account. They are re-exported here
// because the config export and the layout read them from the chat store.
export { ASSIST_ADDED_KEY, LEGACY_SEEN_KEYS, QUEUE_SEEN_KEY, SEEN_KEY, type AssistTab } from "./tickets.svelte";

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
/** A terminal speak line counts as echoed only if its channel line arrives this soon. */
const ECHO_WINDOW_MS = 10000;
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
  active = $state<string>("");
  // Per-channel overrides: colour + notify mode ("all" | "mention" | "none").
  channelPrefs = $state<Record<string, { color?: string; notify?: string }>>(loadChannelPrefs());

  /** Speak lines typed in the terminal, oldest first, waiting for their channel lines to echo. */
  private pendingEcho: { key: string; at: number }[] = [];
  private typingTimers: Record<string, ReturnType<typeof setTimeout>> = {};
  /** Opens a panel by view id. Set from main.ts: dock imports this store. */
  private openPanel: ((view: string) => void) | null = null;

  setPanelOpener(fn: (view: string) => void): void {
    this.openPanel = fn;
    tickets.setPanelOpener(fn);
  }

  /** This session works the staff ticket queue (the server's answer; see the tickets store). */
  get staff(): boolean {
    return tickets.staff;
  }

  /** The server has said whether this session is staff. */
  get staffKnown(): boolean {
    return tickets.staffKnown;
  }

  /** The signed-in account's id, from ticket_role; keys what the shell keeps per account. */
  get account(): number | null {
    return tickets.accountId;
  }

  /** Total unread across channels (per-channel counts already exist). */
  get channelsUnseen(): number {
    let n = 0;
    for (const v of Object.values(this.unread)) n += v ?? 0;
    return n;
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
      default:
        // Every ticket event belongs to the tickets store.
        if (event.startsWith("ticket_")) tickets.handleOob(event, args, kwargs);
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

  /** Ask the server to (re)push the channel list, comms status, ticket role and queue. */
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

  // -- terminal echo of a speak line -------------------------------------
  //
  // With channel echo off, a speak line typed in the terminal (xooc hi) went
  // only to the Channels panel. Arm the echo when the line is typed; the
  // sender's own copy of the channel line (`own`, set by the server) is
  // echoed once, so nothing the server refused, and no one else's line, is
  // shown as said.

  /** Note a line typed in the terminal; a channel speak verb, after client aliases, arms its echo. */
  armEcho(line: string): void {
    const t = triggers.expand((line || "").trim());
    const space = t.indexOf(" ");
    if (space < 0) return;
    const verb = t.slice(0, space).toLowerCase();
    const ch = this.channels.find((c) => (c.speakCmd ?? "").toLowerCase() === verb);
    if (ch && t.slice(space + 1).trim()) this.pendingEcho.push({ key: ch.key, at: Date.now() });
  }

  /** Whether this channel line answers a speak line typed in the terminal (consumes it). */
  takeEcho(key: string, own: boolean): boolean {
    const now = Date.now();
    this.pendingEcho = this.pendingEcho.filter((p) => now - p.at <= ECHO_WINDOW_MS);
    if (!own) return false;
    const i = this.pendingEcho.findIndex((p) => p.key === key);
    if (i < 0) return false;
    this.pendingEcho.splice(i, 1);
    return true;
  }

  // -- login ---------------------------------------------------------------

  /** Forget the last account's tickets and role; a new login starts clean. */
  resetForLogin(): void {
    tickets.reset();
    this.pendingEcho = [];
  }

  /**
   * Forget the account that quit: its tickets and its channel lines. The shell
   * stays drawn under the quit screen, and the next person at the browser
   * could read it there.
   */
  logout(): void {
    this.resetForLogin();
    this.channels = [];
    this.messages = {};
    this.unread = {};
    this.online = {};
    this.topics = {};
    this.typing = {};
    this.pins = {};
    this.mentions = {};
    this.muted = {};
    this.readMark = {};
    this.active = "";
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
