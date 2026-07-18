import { mount } from "svelte";
import App from "./App.svelte";
import "./styles/themes.css";
import "./styles/shell.css";
import "./styles/ansi-palette.css";
import { connection } from "./lib/evennia.svelte";
import { session } from "./lib/session.svelte";
import { scene } from "./lib/scene.svelte";
import { puppets } from "./lib/puppets.svelte";
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
import { dock } from "./lib/dock.svelte";
import { createLegacyEmitter } from "./lib/legacy-emitter";

const OOB_TRACE_KEY = "underspire.trace.oob";

function oobTrace(event: string, detail?: unknown): void {
  try {
    if (localStorage.getItem(OOB_TRACE_KEY) === "1") {
      console.debug("[oob]", event, detail ?? "");
    }
  } catch {
    /* ignore */
  }
}

// Legacy global API so in-game MXP links (`<a onclick="Evennia.msg(...)">`) and
// any code expecting the classic global route to the live socket.
const legacyEmitter = createLegacyEmitter();
(window as any).Evennia = {
  msg: (cmdname: string, args: any[] = [], kwargs: Record<string, any> = {}) =>
    connection.legacyMsg(cmdname, args, kwargs),
  emitter: legacyEmitter,
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
  puppets.apply(env);
});

puppets.setResyncRequester((npcId, revision) => {
  connection.sendCommand(`@sync_puppet_scene ${npcId} ${revision}`);
});

function refreshPuppetManifest() {
  connection
    .request("puppets", "puppet_manifest")
    .then((reply: any) => puppets.setManifest(reply?.puppets ?? []))
    .catch(() => {
      /* non-staff / offline: no roster */
    });
}

connection.on("connection_open", () => {
  connection.sendCommand("@sync_context");
  refreshPuppetManifest();
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
  } else if (event === "web_panel") {
    // Fold a magic-link page into the shell: open its URL in a floating iframe.
    // The iframe carries the shared Django session, so no token is required.
    const spec = (Array.isArray(env.args) ? env.args[0] : env.args) ?? env.kwargs ?? {};
    const url = String(spec.url ?? "");
    if (url) {
      const id = String(spec.id ?? url);
      dock.openIframe(id, String(spec.title ?? "Web"), url);
    }
  } else if (event === "logout") {
    // Server-side @quit: raise the quit menu instead of silently reconnecting.
    const reason = Array.isArray(env.args) ? env.args[0] : env.args;
    connection.markLoggedOut(String(reason ?? "quit"));
  } else if (event === "player_mention") {
    chat.onMention(env.kwargs ?? {});
  } else if (event === "image" || event === "audio" || event === "video" || event === "youtube") {
    const url = Array.isArray(env.args) ? env.args[0] : env.args;
    const kw = env.kwargs ?? {};
    if (!url) return;
    // Room BGM / @music use play_yt; skip youtube URL OOB for playback (avoids dual-OOB races).
    if (event === "youtube") return;
    media.add(event, String(url), { loop: !!(kw.loop ?? kw.looping) });
  } else if (event === "play_yt") {
    const args = Array.isArray(env.args) ? env.args : [];
    const rawId = args[0];
    oobTrace("play_yt", args);
    if (rawId != null) {
      media.playYoutube(String(rawId), parseFloat(String(args[1] ?? 0)), !!(args[2] === 1 || args[2] === true));
    }
  } else if (event === "stop_music") {
    oobTrace("stop_music", env.kwargs ?? {});
    const kw = env.kwargs ?? {};
    const fadeMs = kw.fade_out != null ? Number(kw.fade_out) * 1000 : undefined;
    media.stop(fadeMs);
  } else if (event === "stop_music_now") {
    oobTrace("stop_music_now");
    media.stopNow();
  } else if (event === "STOP_AUDIO") {
    const kw = env.kwargs ?? {};
    const fadeMs = kw.fade_out != null ? Number(kw.fade_out) * 1000 : undefined;
    media.stop(fadeMs);
  } else if (event === "yt_set_loop") {
    const args = Array.isArray(env.args) ? env.args : [];
    media.setYtLoop(!!(args[0] === 1 || args[0] === true));
  } else if (event === "play_music") {
    const url = Array.isArray(env.args) ? env.args[0] : env.args;
    if (url) media.playMusic(String(url));
  } else if (event === "PLAY_AUDIO") {
    const kw = env.kwargs ?? {};
    const url = kw.url ?? (Array.isArray(env.args) ? env.args[0] : env.args);
    if (url) {
      media.playAudio(String(url), {
        loop: !!kw.loop,
        volume: kw.volume != null ? Number(kw.volume) : undefined,
        fadeIn: kw.fade_in != null ? Number(kw.fade_in) : undefined,
      });
    }
  } else if (event === "SET_AUDIO_VOLUME") {
    const kw = env.kwargs ?? {};
    if (kw.volume != null) {
      media.setAudioVolume(Number(kw.volume), kw.fade != null ? Number(kw.fade) : 1);
    }
  } else if (event === "editor_open" || event === "editor_close" || event === "editor_status") {
    legacyEmitter.emit(event, env.args ?? [], env.kwargs ?? {});
  } else if (event.startsWith("community_")) {
    toasts.fromCommunity(event, env.kwargs ?? {});
  }
});

connection.init();

const target = document.getElementById("evennia-shell") ?? document.body;
mount(App, { target });
