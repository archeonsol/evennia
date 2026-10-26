// Browser checks for the Puppets panel: several terminals at once, each with a
// command line of its own that shares the main one's history. Open
// /tests/puppets.html on the Vite dev server, or run `npm run test:browser`.
//
// The real PuppetsPanel is mounted against the real puppet, layout, command and
// settings stores, with the socket stubbed: requests are recorded. Keys are
// dispatched as the browser would; a synthetic key never inserts text, so a
// check that a key was left to the browser looks at whether it was cancelled.

import { mount, tick } from "svelte";

import "../src/styles/themes.css";
import "../src/styles/shell.css";
import PuppetsPanel from "../src/components/PuppetsPanel.svelte";
import { commands } from "../src/lib/commands.svelte";
import { connection } from "../src/lib/evennia.svelte";
import { puppetLayout } from "../src/lib/puppetLayout.svelte";
import { puppets } from "../src/lib/puppets.svelte";
import { settings } from "../src/lib/settings.svelte";

declare global {
  interface Window {
    __puppetsTest?: { done: boolean; failures: number; results: string[] };
  }
}

const results: string[] = [];
let failures = 0;
const pageErrors: string[] = [];
function check(name: string, cond: boolean, detail = ""): void {
  if (cond) results.push(`PASS ${name}`);
  else {
    failures++;
    results.push(`FAIL ${name}${detail ? `: ${detail}` : ""}`);
  }
}
window.addEventListener("error", (e) => pageErrors.push(String(e.message)));
window.addEventListener("unhandledrejection", (e) => pageErrors.push(String(e.reason)));

async function settle(frames = 3): Promise<void> {
  for (let i = 0; i < frames; i++) {
    await tick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  }
  await tick();
}

interface Sent {
  ns: string;
  action: string;
  data: any;
}
const sent: Sent[] = [];
let failNext = false;
(connection as any).state = "open";
connection.sendCommand = () => {
  throw new Error("a puppet must never send through the main command line");
};
(connection as any).request = async (ns: string, action: string, data: any) => {
  sent.push({ ns, action, data });
  if (failNext) {
    failNext = false;
    throw new Error("refused");
  }
  return { names: [] };
};

// A clean slate: nothing persisted from an earlier run of this page.
commands.useAccount(1);
commands.recent = [];
puppetLayout.useAccount(1);
puppetLayout.open = null;
puppetLayout.arrange = "grid";
settings.keepCommand = false;
settings.historyKeys = "edge";
settings.echoCommands = false;

const ROSTER = [
  { npc_id: 11, slot: 2, name: "Bartender" },
  { npc_id: 12, slot: 3, name: "Bouncer" },
  { npc_id: 13, slot: 4, name: "Courier" },
  { npc_id: 14, slot: 5, name: "Doctor" },
  { npc_id: 15, slot: 6, name: "Engineer" },
];
puppets.setManifest(ROSTER);
for (const p of ROSTER) {
  puppets.apply({
    target: "puppets",
    meta: { npc_id: p.npc_id, slot: p.slot, name: p.name, revision: 1 },
    ops: [{ op: "set", path: `/${p.npc_id}/scene`, value: { room: { name: `Room of ${p.name}` }, occupants: [], exits: [] } }],
  });
}
function feed(npcId: number, body: string, revision: number): void {
  puppets.apply({
    target: "puppets",
    meta: { npc_id: npcId, slot: 2, name: "x", revision },
    ops: [{ op: "add", path: `/${npcId}/feed/-`, value: { body, meta: { revision } } }],
  });
}
for (const p of ROSTER) feed(p.npc_id, `${p.name} looks around.`, 2);

const host = document.getElementById("host")!;
mount(PuppetsPanel, { target: host });

const tiles = () => [...host.querySelectorAll<HTMLElement>(".tile")];
const tile = (id: number) => host.querySelector<HTMLElement>(`.tile[data-npc="${id}"]`)!;
const field = (id: number) => tile(id)?.querySelector<HTMLTextAreaElement>("textarea.term-input") ?? null;
const chip = (id: number) => [...host.querySelectorAll<HTMLButtonElement>(".chip")].find((c) => c.title.includes(`#${id}`))!;
const button = (scope: ParentNode, text: string) =>
  [...scope.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === text)!;
const columns = () => getComputedStyle(host.querySelector(".grid")!).gridTemplateColumns.split(" ").length;

async function type(el: HTMLTextAreaElement | HTMLInputElement, text: string, caret = text.length): Promise<void> {
  el.focus();
  el.value = text;
  el.setSelectionRange(caret, caret);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  await settle();
}

async function press(el: Element, key: string, mods: KeyboardEventInit = {}): Promise<boolean> {
  const ev = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...mods });
  el.dispatchEvent(ev);
  await settle();
  return ev.defaultPrevented;
}

async function lineBreak(el: Element): Promise<boolean> {
  const ev = new InputEvent("beforeinput", { inputType: "insertLineBreak", bubbles: true, cancelable: true });
  el.dispatchEvent(ev);
  await settle();
  return ev.defaultPrevented;
}

const lastRequest = () => sent.at(-1);

async function run(): Promise<void> {
  await settle();

  // -- the layout -----------------------------------------------------------
  check("every puppet has a terminal until the player chooses", tiles().length === 5, String(tiles().length));
  check("each terminal has a command line of its own", [11, 12, 13, 14, 15].every((id) => !!field(id)));
  check("the grid is three columns at 900px", columns() === 3, String(columns()));
  button(host, "Rows").click();
  await settle();
  check("rows stack in one column", columns() === 1, String(columns()));
  button(host, "Columns").click();
  await settle();
  check("columns are held to what the width allows", columns() === 3, String(columns()));
  button(host, "Grid").click();
  await settle();
  check("the arrangement is the setting", puppetLayout.arrange === "grid");
  const narrow = document.getElementById("host")!;
  narrow.style.width = "420px";
  await settle(6);
  check("a narrow panel falls back to one column", columns() === 1, String(columns()));
  narrow.style.width = "900px";
  await settle(6);

  // -- a command line of its own ---------------------------------------------
  const a = field(12)!;
  await press(a, "Enter");
  check("an empty line sends no request", sent.length === 0, String(sent.length));
  await type(a, "say hello there");
  check("Enter is taken", await press(a, "Enter"));
  check("it goes to that puppet", lastRequest()?.action === "puppet_cmd" && lastRequest()?.data.npc_id === 12, JSON.stringify(lastRequest()));
  check("through the puppets endpoint", lastRequest()?.ns === "puppets");
  check("with the line as typed", lastRequest()?.data.cmd === "say hello there", JSON.stringify(lastRequest()?.data));
  check("the line is cleared", a.value === "", a.value);
  check("only that puppet was sent to", sent.length === 1, String(sent.length));
  check("the line joined the shared history", commands.recent[0] === "say hello there", String(commands.recent[0]));

  // -- history is shared across terminals ------------------------------------
  const b = field(14)!;
  await type(b, "half a pose");
  check("Up is taken", await press(b, "ArrowUp"));
  check("another puppet's Up finds the command", b.value === "say hello there", b.value);
  check("Down past the newest is taken", await press(b, "ArrowDown"));
  check("Down brings the typed line back", b.value === "half a pose", b.value);
  check("Down on the typed line is left to the browser", !(await press(b, "ArrowDown")));
  await press(b, "ArrowUp");
  await press(b, "Enter");
  check("a recalled command goes to the puppet it was recalled in", lastRequest()?.data.npc_id === 14 && lastRequest()?.data.cmd === "say hello there", JSON.stringify(lastRequest()));
  check("the typed line comes back after it", b.value === "half a pose", b.value);
  check("the other terminals were not touched", field(11)!.value === "" && field(12)!.value === "");

  // The main command line's history is the same list.
  commands.recent = ["look", ...commands.recent];
  await type(field(15)!, "");
  await press(field(15)!, "ArrowUp");
  check("a command typed in the main line is found too", field(15)!.value === "look", field(15)!.value);
  await type(field(15)!, "");

  // -- the keys ---------------------------------------------------------------
  const c = field(13)!;
  await type(c, "inv");
  check("Tab is taken", await press(c, "Tab"));
  check("Tab completes the verb", c.value === "inventory", c.value);
  await type(c, "");
  await press(c, "Escape"); // any other key ends the completion cycle, as typing would
  check("Tab on an empty line is left to the browser", !(await press(c, "Tab")));

  check("Page Up pages the feed and is taken", await press(c, "PageUp"));
  check("Page Down too", await press(c, "PageDown"));
  check("neither sends", sent.length === 2, String(sent.length));

  check("Shift+Enter is a line break, left to the browser", !(await press(c, "Enter", { shiftKey: true })));
  check("so the line break it asks for is let through", !(await lineBreak(c)));
  await type(c, "say again");
  check("a line break nobody asked for is taken", await lineBreak(c));
  check("and sends", sent.length === 3 && lastRequest()?.data.npc_id === 13 && lastRequest()?.data.cmd === "say again", JSON.stringify(lastRequest()));
  await type(c, "");

  await type(c, "x");
  await press(c, "r", { ctrlKey: true });
  const search = tile(13).querySelector<HTMLInputElement>(".rs-input");
  check("Ctrl-R opens the history search", !!search);
  if (search) {
    await type(search, "hello");
    check("it finds the match", tile(13).querySelector(".rs-match")?.textContent === "say hello there", tile(13).querySelector(".rs-match")?.textContent ?? "");
    await press(search, "Enter");
    check("Enter takes it", field(13)!.value === "say hello there", field(13)!.value);
    check("and returns the keys to the command line", document.activeElement === field(13));
  }
  await type(field(13)!, "");

  // -- echo and failure -------------------------------------------------------
  settings.echoCommands = true;
  await type(a, "emote nods.");
  await press(a, "Enter");
  check("the sent line is echoed in that terminal's feed", /> emote nods\./.test(tile(12).querySelector(".term-feed")?.textContent ?? ""), tile(12).querySelector(".term-feed")?.textContent ?? "");
  check("and not in another's", !/> emote nods\./.test(tile(11).querySelector(".term-feed")?.textContent ?? ""));
  settings.echoCommands = false;
  failNext = true;
  await type(a, "say will not arrive");
  await press(a, "Enter");
  check("a request that fails is said in the feed", /Not sent/.test(tile(12).querySelector(".term-feed")?.textContent ?? ""), tile(12).querySelector(".term-feed")?.textContent ?? "");

  // -- showing and hiding -----------------------------------------------------
  check("a chip is lit for each shown puppet", chip(11).getAttribute("aria-pressed") === "true");
  chip(11).click();
  await settle();
  check("hiding a chip removes its terminal", tiles().length === 4 && !tile(11), String(tiles().length));
  check("and its chip goes dark", chip(11).getAttribute("aria-pressed") === "false");
  feed(11, "Someone waves at Bartender.", 3);
  await settle();
  check("a hidden puppet badges on its chip", !!chip(11).querySelector(".badge"));
  feed(12, "Someone waves at Bouncer.", 3);
  await settle();
  check("a shown puppet stays quiet", !chip(12).querySelector(".badge"));
  chip(11).click();
  await settle();
  check("showing it brings the terminal back", tiles().length === 5 && !!tile(11));
  check("and reads its news", !chip(11).querySelector(".badge"));
  check("a terminal just asked for takes the keys", document.activeElement === field(11), String(document.activeElement?.tagName));

  button(host, "Hide all").click();
  await settle();
  check("Hide all leaves no terminal", tiles().length === 0);
  check("and says so", /No terminal shown/.test(host.textContent ?? ""));
  button(host, "Show all").click();
  await settle();
  check("Show all brings every one back", tiles().length === 5);

  button(tile(13), "Expand").click();
  await settle();
  check("Expand gives one terminal the panel", tiles().length === 1 && !!tile(13), String(tiles().length));
  check("the others are still on their chips", chip(12).getAttribute("aria-pressed") === "true");
  button(tile(13), "Restore").click();
  await settle();
  check("Restore returns them", tiles().length === 5);

  // -- a puppet the player drops ----------------------------------------------
  puppets.setManifest(ROSTER.filter((p) => p.npc_id !== 15));
  await settle();
  check("a puppet that leaves the roster loses its terminal", tiles().length === 4 && !tile(15), String(tiles().length));

  check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));
}

function finish(): void {
  const pre = document.getElementById("results")!;
  pre.textContent = results.join("\n");
  window.__puppetsTest = { done: true, failures, results };
}

// A check that throws must still report, or the runner only sees a timeout.
run()
  .catch((error) => check("the checks ran to the end", false, String(error?.stack ?? error)))
  .finally(finish);
