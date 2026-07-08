import { mount } from "svelte";
import App from "./App.svelte";
import "./styles/themes.css";
import "./styles/shell.css";
import "./styles/ansi-palette.css";
import { connection } from "./lib/evennia.svelte";
import { session } from "./lib/session.svelte";
import { scene } from "./lib/scene.svelte";
import { settings } from "./lib/settings.svelte";
import { commands } from "./lib/commands.svelte";
import { macros } from "./lib/macros.svelte";
import { triggers } from "./lib/triggers.svelte";
import { keybinds } from "./lib/keybinds.svelte";
import { panelPrefs } from "./lib/panelPrefs.svelte";
import { routing } from "./lib/routing.svelte";
import { chat } from "./lib/chat.svelte";
import { toasts } from "./lib/toasts.svelte";
import { notify } from "./lib/notify.svelte";
import { renderNodeHtml } from "./lib/render";
import { media } from "./lib/media.svelte";
import { ui } from "./lib/ui.svelte";

// Legacy global API so in-game MXP links (`<a onclick="Evennia.msg(...)">`) and
// any code expecting the classic global route to the live socket.
(window as any).Evennia = {
  msg: (cmdname: string, args: any[] = [], kwargs: Record<string, any> = {}) =>
    connection.legacyMsg(cmdname, args, kwargs),
};

// MXP command links are <a id="mxplink" href="#" onclick="Evennia.msg(...)">.
// The onclick fires the command; stop the "#" from scrolling the page.
document.addEventListener("click", (e) => {
  const a = (e.target as HTMLElement)?.closest?.("a#mxplink");
  if (a) e.preventDefault();
});

// Apply persisted theme + CRT settings to <html> before the shell mounts; load
// command history + macros.
settings.init();
commands.init();
macros.init();
triggers.init();
keybinds.init();
panelPrefs.init();
routing.init();
notify.init();

// Direct-message-ish kinds that deserve an attention ping when unfocused.
const TELL_KINDS = new Set(["tell", "whisper", "page", "say_to"]);

// Azaban protocol: typed envelopes. `text`/`prompt` carry server-rendered HTML
// (parse_html); the palette CSS colours the classes. `render` carries structured
// RenderNodes - we render each node's html and make its character refs clickable.

connection.on("text", (env) => {
  const kind = env.kind ?? "text";
  session.append(String(env.html ?? ""), kind);
  if (TELL_KINDS.has(String(kind).toLowerCase())) {
    notify.ping("New message", session.lines[session.lines.length - 1]?.text ?? "");
  }
});

connection.on("prompt", (env) => {
  session.setPrompt(String(env.html ?? ""));
});

connection.on("render", (env) => {
  // Be tolerant of the payload shape: a list of nodes, or a single node.
  const nodes = Array.isArray(env.nodes) ? env.nodes : env.nodes ? [env.nodes] : [];
  for (const node of nodes) {
    if (node) session.append(renderNodeHtml(node), node.msg_type ?? "text");
  }
});

// Scene-model deltas keep the room panel in sync (reactive, off the log).
connection.on("patch", (env) => {
  scene.apply(env.target, env.ops ?? []);
});

// Typed OOB events - routed by name. Channel/chat events feed the chat store;
// other events can register here as consumers land.
connection.on("oob", (env) => {
  const event = String(env.event ?? "");
  if (
    event.startsWith("channel_") ||
    event === "channels_list" ||
    event === "assist_inbox" ||
    event === "assist_thread" ||
    event.startsWith("ticket_")
  ) {
    chat.handleOob(event, env.args ?? [], env.kwargs ?? {});
  } else if (event === "ui_component") {
    const comp = Array.isArray(env.args) ? env.args[0] : env.args;
    if (comp) ui.set(comp);
  } else if (event === "ui_remove") {
    const id = (Array.isArray(env.args) ? env.args[0] : env.args)?.id;
    if (id) ui.remove(String(id));
  } else if (event === "logout") {
    // Server-side @quit: raise the quit menu instead of silently reconnecting.
    const reason = Array.isArray(env.args) ? env.args[0] : env.args;
    connection.markLoggedOut(String(reason ?? "quit"));
  } else if (event === "player_mention") {
    chat.onMention(env.kwargs ?? {});
  } else if (event === "image" || event === "audio" || event === "video" || event === "youtube") {
    // Server multimedia: target.msg(image="url") etc. Args = [url] (optionally a
    // {type} kwargs dict, ignored here). Render an inline media line in the log.
    const url = Array.isArray(env.args) ? env.args[0] : env.args;
    if (url) media.add(event, String(url));
  } else if (event.startsWith("community_")) {
    toasts.fromCommunity(event, env.kwargs ?? {});
  }
});

connection.init();

const target = document.getElementById("evennia-shell") ?? document.body;
mount(App, { target });
