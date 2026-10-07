// Design preview: the whole shell, mounted with no server, seeded with a
// believable session so the chrome can be judged with content in it.
//
//   npx vite --port 5300   then open /tests/preview.html
//   ?panel=tickets  opens My Tickets; ?theme=<id> picks a theme.
//   ?help=<query>   opens the help panel on a recorded page ("" is the index).
//   ?settings=<view>  opens Settings, on a page when one is named (reading).
//
// Nothing here is under test; the browser suites live beside it.

import { mount } from "svelte";

import "../src/styles/glyph-fallback.css";
import "../src/styles/themes.css";
import "../src/styles/shell.css";
import "../src/styles/ansi-palette.css";
import App from "../src/App.svelte";
import { chat } from "../src/lib/chat.svelte";
import { compose } from "../src/lib/compose.svelte";
import { connection } from "../src/lib/evennia.svelte";
import { scene } from "../src/lib/scene.svelte";
import { session } from "../src/lib/session.svelte";
import { settings } from "../src/lib/settings.svelte";
import { dock } from "../src/lib/dock.svelte";
import { toasts } from "../src/lib/toasts.svelte";
import { help, type HelpPage } from "../src/lib/help.svelte";
import { lore } from "../src/lib/lore.svelte";
import helpFixtures from "./help-fixtures.json";
import loreFixtures from "./lore-tips.json";

const params = new URLSearchParams(location.search);
try {
  localStorage.clear();
} catch {
  /* private mode */
}
const theme = params.get("theme");
if (theme) (settings as any).theme = theme;

const now = Math.floor(Date.now() / 1000);
const mine = [
  { id: "a1b2c3d4e5", short_id: "a1b2c3d4", kind: "request", label: "Request", status: "waiting", subject: "Door in the Spire won't open", preview: "Try it now.", updated: now - 240, approvable: false },
  { id: "f6a7b8c9d0", short_id: "f6a7b8c9", kind: "puppet", label: "Puppet", status: "pending", subject: "Talk to the fixer at Kettle's", preview: "Goal: find the courier", updated: now - 5400, approvable: false },
  { id: "e1f2a3b4c5", short_id: "e1f2a3b4", kind: "request", label: "Request", status: "closed", subject: "Sheet shows wrong reflex", preview: "Fixed, thanks.", updated: now - 86400 * 3, approvable: false },
];
const thread = {
  ...mine[0],
  messages: [
    { origin: "player", sender: "Vesna", text: "The maintenance door on level 4 of the Spire won't open, even with the key.", ts: now - 900 },
    { origin: "system", sender: "System", text: "Mira is handling this.", ts: now - 600 },
    { origin: "staff", sender: "Mira", text: "Fixed the lock. Try it now.", ts: now - 240 },
  ],
};
// The game's reading settings, kept in memory the way the account keeps them.
const readingColours = [
  { name: "red", code: "|r" },
  { name: "green", code: "|g" },
  { name: "yellow", code: "|y" },
  { name: "cyan", code: "|c" },
  { name: "orange", code: "|520" },
  { name: "violet", code: "|552" },
];
let reading = {
  you: true,
  speech: "",
  speech_name: "",
  aimed_colour: "",
  aimed_colour_name: "",
  aimed_marker: false,
  spacing: false,
};
const colourName = (code: string) => readingColours.find((c) => c.code === code)?.name ?? "";
function readingSet(setting: string, value: string) {
  if (setting === "reset") {
    reading = { you: true, speech: "", speech_name: "", aimed_colour: "", aimed_colour_name: "", aimed_marker: false, spacing: false };
  } else if (setting === "you" || setting === "spacing") {
    reading = { ...reading, [setting]: value === "on" };
  } else if (setting === "speech") {
    const code = value === "off" ? "" : value;
    reading = { ...reading, speech: code, speech_name: colourName(code) };
  } else if (setting === "aimed") {
    const words = value.split(" ").filter((w) => w && w !== "off");
    const code = words.find((w) => w !== "marker") ?? "";
    reading = { ...reading, aimed_colour: code, aimed_colour_name: colourName(code), aimed_marker: words.includes("marker") };
  }
  return { settings: reading, colours: readingColours };
}
const helpViews = helpFixtures.views as Record<string, HelpPage>;
const helpSearches = helpFixtures.searches as Record<string, HelpPage>;
(connection as any).request = async (_ns: string, action: string, data?: any) => {
  const q = String(data?.query ?? "");
  if (action === "help_view") return helpViews[q] ?? { kind: "not_found", query: q, hits: [], suggestions: [] };
  if (action === "help_search") return helpSearches[q] ?? { kind: "search", query: q, hits: [] };
  if (action === "help_prefs") return { panel: true };
  if (action === "lore_tips") return loreFixtures;
  if (action === "reading_get") return { settings: reading, colours: readingColours };
  if (action === "reading_set") return readingSet(String(data?.setting ?? ""), String(data?.value ?? ""));
  if (action === "my_tickets") return { tickets: mine };
  if (action === "my_ticket") return thread;
  if (action === "ticket_list") return { tickets: [] };
  return {};
};
// The game answers a compose preview with the post as each reader sees it. A post
// of several lines keeps them, with a gutter before every line after the first.
compose.setPreviewSender((line) => {
  const post = line.replace(/^@preview_rp \w+ /, "").split("|/").join("\n|x│|n ");
  compose.applyPreview({ you: `You ${post}`, room: `A lean courier ${post}` });
});
(connection as any).init = () => {};
connection.state = "open";

// The game's lore words, as its `lore:lore_tips` request answers (tests/lore-tips.json
// is that answer, written from the game's own registry). main.ts asks on connect; here
// they are set up front so the lines seeded below are marked as they land.
lore.init();
lore.setTips(loreFixtures);

scene.room = { name: "Kettle's Noodle Counter" } as any;
scene.present = true;

chat.handleOob("channels_list", [
  { key: "public", name: "Public", color: "#7fd3a8" },
  { key: "newbie", name: "Newbie", color: "#e0b35a" },
  { key: "ooc", name: "OOC" },
], {});
const said = [
  ["public", "Sanon", "anyone up for a run to the flooded stacks tonight"],
  ["public", "V3R1TAS", "only if someone else brings the lights this time"],
  ["newbie", "Kettle", "welcome in. type |whelp start|n to get your bearings"],
  ["public", "negative", "looking for a tailor, simple everyday clothing"],
];
said.forEach(([channel, sender, text], i) =>
  chat.handleOob("channel_msg", [], { channel, sender, text, ts: now - 600 + i * 90, msg_id: `m${i}` }),
);

const lines: [string, string][] = [
  ["look", "<span class=\"ansi-bright-white\">Kettle's Noodle Counter</span>"],
  ["look", "Steam rolls off three copper vats behind a counter scarred by a decade of elbows. A strip of red neon buzzes over the pass, throwing the rain-streaked window into a smear of colour. Somewhere under the floor, the city's pumps thud like a slow heart."],
  ["look", "<span class=\"ansi-cyan\">Exits:</span> north, down"],
  ["look", "A lean courier in a rain-slick coat leans on the counter, and Kettle is here, ladling broth."],
  ["say", "The lean courier says, \"You're late. The stacks flooded an hour ago.\""],
  ["say", "You say, \"Then we go in wet.\""],
  ["text", "Kettle slides a bowl across the counter without looking up."],
  ["say", "Kettle says, \"Eat. Saint Marrow knows you need it, and so does the Hum.\""],
  ["text", "A pilgrim in a grey coat kneels by the door and prays to Marrow, then asks the courier about the Outriders."],
  ["combat", "<span class=\"ansi-red\">A drone clips the window with a burst of static. Glass spiders across the pane.</span>"],
  ["text", "The courier pockets a folded chit and nods toward the stairs down."],
  ["handset", "[Oct 05 20:11] Mira: you still coming to the stacks?"],
  ["looc", "<span class=\"ansi-cyan\">[LOOC]</span> The lean courier: brb, kettle's boiling over"],
];
for (const [cat, html] of lines) session.append(`<span>${html}</span>`, cat);

const target = document.getElementById("evennia-shell")!;
mount(App, { target });

// Skip the boot flourish so the screenshot shows the shell.
setTimeout(() => {
  document.querySelector<HTMLElement>(".boot")?.click();
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  if (theme) document.documentElement.setAttribute("data-theme", theme);
  const panel = params.get("panel");
  if (panel) setTimeout(() => dock.openView(panel), 200);
  const helpQuery = params.get("help");
  if (helpQuery !== null) {
    setTimeout(() => {
      dock.openHelp();
      void help.open(helpQuery);
    }, 250);
  }
  if (params.has("thread")) {
    setTimeout(() => document.querySelector<HTMLElement>(".mine .sh-row")?.click(), 700);
  }
  if (params.has("settings")) {
    window.dispatchEvent(new CustomEvent("underspire:settings", { detail: { view: params.get("settings") || undefined } }));
  }
  if (params.has("quit")) (connection as any).loggedOut = true;
  if (params.has("compose")) setTimeout(() => document.querySelector<HTMLElement>(".compose-btn")?.click(), 300);
  if (params.has("toast")) {
    setTimeout(() => toasts.push("ticket", "Mira replied to your ticket", "Fixed the lock. Try it now.", 60000, false), 400);
  }
}, 50);
