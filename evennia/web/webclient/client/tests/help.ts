// Browser checks for the help panel. Run with `npm run test:browser`.
//
// The real panel and store run against a stubbed connection answering from
// fixtures generated from the game's help files (help-fixtures.json). Every
// RPC is recorded, and so is any typed command: browsing help must never type
// a command, or the page would print into the terminal.

import { mount, tick } from "svelte";

import "../src/styles/themes.css";
import "../src/styles/shell.css";
import "../src/styles/ansi-palette.css";
import HelpPanel from "../src/components/HelpPanel.svelte";
import { help, type HelpPage } from "../src/lib/help.svelte";
import { connection } from "../src/lib/evennia.svelte";
import fixtures from "./help-fixtures.json";

declare global {
  interface Window {
    __helpTest?: { done: boolean; failures: number; results: string[] };
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

async function settle(frames = 4): Promise<void> {
  for (let i = 0; i < frames; i++) {
    await tick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  }
  await tick();
}
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const views = fixtures.views as Record<string, HelpPage>;
const searches = fixtures.searches as Record<string, HelpPage>;
const calls: { action: string; data: any }[] = [];
const commands: string[] = [];
(connection as any).state = "open";
(connection as any).request = async (_ns: string, action: string, data: any) => {
  calls.push({ action, data });
  const q = String(data?.query ?? "");
  if (action === "help_view") return views[q] ?? { kind: "not_found", query: q, hits: [], suggestions: [] };
  if (action === "help_search") return searches[q] ?? { kind: "search", query: q, hits: [] };
  if (action === "help_prefs") return { panel: true };
  throw new Error(`unexpected ${action}`);
};
connection.sendCommand = (line: string) => {
  commands.push(line);
};

const root = document.getElementById("panel")!;
mount(HelpPanel, { target: root });
const $ = <T extends Element = HTMLElement>(sel: string) => root.querySelector<T>(sel);
const $$ = (sel: string) => Array.from(root.querySelectorAll<HTMLElement>(sel));
const views_of = (action: string) => calls.filter((c) => c.action === action).map((c) => c.data?.query);

async function run(): Promise<void> {
  await settle();
  await wait(50);
  await settle();

  // Opening with nothing loaded fetches the list of topics.
  check("opens on the index", views_of("help_view")[0] === "", JSON.stringify(calls));
  const cats = $$(".catname").map((b) => b.textContent);
  check("index lists categories in reading order", cats[0] === "General" && cats.includes("Lore"), String(cats));
  check("index hides staff categories", !cats.includes("Admin"));

  // A topic chip opens the topic, over the RPC.
  const chip = $$(".chip").find((b) => b.textContent === "inventory");
  chip?.click();
  await settle();
  check("chip asks for the topic", views_of("help_view").at(-1) === "inventory");
  check("topic title shows", $(".title")?.textContent === "Inventory", $(".title")?.textContent ?? "");
  const secs = $$(".sec h3").map((h) => h.textContent);
  check("every section renders", secs.includes("Wear") && secs.includes("Frisk"), String(secs));
  check("long topics get a section list", $$(".toc .chip").length === secs.length);
  check("headings are not printed as # lines", !$(".body")!.textContent!.includes("# wear"));

  // A help reference in the text is an in-panel link.
  const link = $("a.help-link") as HTMLAnchorElement | null;
  check("help references become links", !!link && !!link.dataset.help, link?.outerHTML ?? "none");
  const target = link?.dataset.help ?? "";
  link?.click();
  await settle();
  check("a link opens its topic over the RPC", views_of("help_view").at(-1) === target, target);

  // Back and forward walk the history.
  ($(".nav[aria-label='Back']") as HTMLButtonElement).click();
  await settle();
  check("back returns to the previous topic", $(".title")?.textContent === "Inventory", $(".title")?.textContent ?? "");
  ($(".nav[aria-label='Forward']") as HTMLButtonElement).click();
  await settle();
  check("forward goes on again", help.page?.query === target, String(help.page?.query));

  // A typed `help wear` arrives as a push: the panel shows the topic at the section.
  help.show(views["wear"]);
  await settle(6);
  const focus = $(".sec.focus h3")?.textContent;
  check("a pushed section page focuses its section", focus === "Wear", String(focus));

  // Search as you type, then Enter to open.
  const input = $("input.search") as HTMLInputElement;
  input.value = "bank";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await wait(260);
  await settle();
  check("typing searches over the RPC", views_of("help_search").includes("bank"), JSON.stringify(calls.slice(-3)));
  const rows = $$(".row .k").map((r) => r.textContent);
  check("search shows ranked matches", rows[0]?.startsWith("economy") ?? false, String(rows));
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await settle();
  check("Enter opens the typed query", views_of("help_view").at(-1) === "bank");

  // A category row list.
  help.show(views["category combat"]);
  await settle();
  check("category page lists its topics", $$(".row .k").some((k) => k.textContent === "firearms"));

  // Not found offers close spellings.
  help.show(views["wera"]);
  await settle();
  check("not found suggests close spellings", $$(".chip").some((c) => c.textContent === "wear"));

  // The pose example keeps its lines; prose reflows.
  help.show(views["roleplaying"]);
  await settle();
  const pose = $("#help-sec-pose .text")?.innerHTML ?? "";
  const seeAt = pose.indexOf("You see:");
  const othersAt = pose.indexOf("Others see:");
  check(
    "example output keeps its lines",
    seeAt > 0 && othersAt > seeAt && pose.slice(seeAt, othersAt).includes("<br>"),
    pose.slice(0, 200),
  );
  const prose = $("#help-sec-say .text")?.innerHTML ?? "";
  check("hard-wrapped prose reflows", !prose.includes("everyone in the<br>"), prose.slice(0, 160));

  check("browsing never typed a command", commands.length === 0, commands.join(" | "));
  check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));

  // Leave a readable page on screen for a screenshot.
  help.show(views["wear"]);
  await settle();

  const pre = document.getElementById("results")!;
  pre.textContent = results.join("\n");
  window.__helpTest = { done: true, failures, results };
}

void run();
