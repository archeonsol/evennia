// Browser checks for the lore tooltips: the words the log underlines, and the note over
// the one under the pointer. The real store and component run against a real document,
// with the game's own answer (tests/lore-tips.json) as the words. Run with
// `npm run test:browser`.

import { mount } from "svelte";

import "../src/styles/glyph-fallback.css";
import "../src/styles/themes.css";
import "../src/styles/shell.css";
import "../src/styles/ansi-palette.css";
import LoreTip from "../src/components/LoreTip.svelte";
import { connection } from "../src/lib/evennia.svelte";
import { lore } from "../src/lib/lore.svelte";
import { settings } from "../src/lib/settings.svelte";
import fixtures from "./lore-tips.json";

declare global {
  interface Window {
    __loreTest?: { done: boolean; failures: number; results: string[] };
  }
}

const results: string[] = [];
let failures = 0;
function check(name: string, cond: boolean, detail = ""): void {
  if (cond) results.push(`PASS ${name}`);
  else {
    failures++;
    results.push(`FAIL ${name}${detail ? `: ${detail}` : ""}`);
  }
}
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const note = () => document.querySelector<HTMLElement>(".lore-tip");
const word = (id: string) => document.querySelector<HTMLElement>(`.lore[data-lore="${id}"]`)!;

function pointer(type: string, target: Element, kind = "mouse", extra: PointerEventInit = {}): void {
  const r = target.getBoundingClientRect();
  target.dispatchEvent(
    new PointerEvent(type, {
      bubbles: type !== "pointerenter" && type !== "pointerleave",
      pointerType: kind,
      clientX: r.left + r.width / 2,
      clientY: r.top + r.height / 2,
      ...extra,
    }),
  );
}

const tip = (id: string) => fixtures.tips.find((t) => t.id === id)!;

async function run(): Promise<void> {
  settings.init();
  settings.screenreader = false;
  settings.loreTips = true;
  lore.init();
  lore.setTips(fixtures);
  mount(LoreTip, { target: document.body });

  const log = document.getElementById("log")!;
  log.innerHTML = [
    `<div>${lore.wrap("A pilgrim kneels and prays to Marrow, then asks about the Outriders.")}</div>`,
    `<div>${lore.wrap("Nothing here is lore, just a plain line of text.")}</div>`,
    `<div>${lore.wrap("The Hum is low tonight.")}</div>`,
    `<div id="edge">${lore.wrap("by Saint Wren")}</div>`,
  ].join("");
  await wait(50);

  const marrow = word("saint_marrow");
  check("the game's words are known", lore.ready);
  check("a lore word in a line is marked", !!marrow && marrow.textContent === "Marrow");
  check("a line with no lore word is left alone", document.querySelectorAll("#log > div")[1].querySelector(".lore") === null);
  check("a sentence-opening plain word is not marked", document.querySelectorAll("#log > div")[2].querySelector(".lore")?.textContent === "The Hum");

  const style = getComputedStyle(marrow);
  check("a lore word is underlined", style.textDecorationLine.includes("underline"), style.textDecorationLine);
  check("the underline is dotted", style.textDecorationStyle === "dotted", style.textDecorationStyle);
  check("the pointer over a lore word is the help cursor", style.cursor === "help", style.cursor);

  // A hover opens the note after a pause.
  pointer("pointerover", marrow);
  await wait(100);
  check("a hover does not open the note at once", note() === null);
  await wait(300);
  const open = note();
  check("a hover opens the note", !!open);
  check("the note carries the word's title", open?.querySelector(".title")?.textContent === tip("saint_marrow").title, open?.querySelector(".title")?.textContent ?? "");
  check("the note carries the written blurb", open?.querySelector(".blurb")?.textContent === tip("saint_marrow").blurb);
  check("the note is shown, not left hidden", open !== null && getComputedStyle(open).visibility === "visible");
  const box = open!.getBoundingClientRect();
  const w = marrow.getBoundingClientRect();
  check("the note sits over the word when there is room", box.bottom <= w.top + 0.5, `${box.bottom} vs ${w.top}`);
  check("the note stays inside the window", box.left >= 0 && box.right <= window.innerWidth && box.top >= 0, JSON.stringify(box));
  check("the note is in the tooltip role", open!.getAttribute("role") === "tooltip");
  // Small, so it does not bury the text it sits over: no wider than 24 em of its own
  // text, and its text smaller than the log's but not below 10px, which is where a
  // monospace face stops being comfortable to read.
  const noteFont = parseFloat(getComputedStyle(open!).fontSize);
  const logFont = parseFloat(getComputedStyle(marrow).fontSize);
  check("the note is compact", box.width <= noteFont * 24.5 && box.height <= 175, `${Math.round(box.width)}x${Math.round(box.height)} at ${noteFont}px`);
  check("the note's text is smaller than the log's", noteFont < logFont, `${noteFont}px vs ${logFont}px`);
  check("the note's text is still readable", noteFont >= 10, `${noteFont}px`);

  // Leaving closes it after a moment, so the pointer can cross onto the note.
  pointer("pointerout", marrow, "mouse", { relatedTarget: document.body });
  await wait(80);
  check("leaving a word leaves the note up for a moment", note() !== null);
  await wait(300);
  check("leaving a word closes the note", note() === null);

  // Resting on the note keeps it open.
  pointer("pointerover", marrow);
  await wait(400);
  pointer("pointerout", marrow, "mouse", { relatedTarget: note() });
  pointer("pointerenter", note()!);
  await wait(400);
  check("resting on the note keeps it open", note() !== null);
  pointer("pointerleave", note()!);
  await wait(350);
  check("leaving the note closes it", note() === null);

  // Moving to another word switches the note.
  pointer("pointerover", marrow);
  await wait(400);
  pointer("pointerout", marrow, "mouse", { relatedTarget: word("outriders") });
  pointer("pointerover", word("outriders"));
  await wait(200);
  check("moving to another word switches the note", note()?.querySelector(".title")?.textContent === tip("outriders").title, note()?.querySelector(".title")?.textContent ?? "none");
  lore.hide(true);

  // A note near the right edge is pulled back inside the window.
  const edge = word("saint_wren");
  edge.style.position = "absolute";
  edge.style.left = `${window.innerWidth - 30}px`;
  pointer("pointerover", edge);
  await wait(400);
  const nearEdge = note()!.getBoundingClientRect();
  check("a note by the window's edge stays inside it", nearEdge.right <= window.innerWidth && nearEdge.left >= 0, JSON.stringify(nearEdge));
  lore.hide(true);
  edge.style.position = "";

  // A word with no room above it gets the note below.
  const top = word("the_hum");
  top.style.position = "fixed";
  top.style.top = "4px";
  top.style.left = "40px";
  pointer("pointerover", top);
  await wait(400);
  const below = note()!.getBoundingClientRect();
  check("a word at the top of the window gets its note below", below.top >= top.getBoundingClientRect().bottom - 0.5, `${below.top} vs ${top.getBoundingClientRect().bottom}`);
  lore.hide(true);
  top.style.position = "";

  // A tap opens the note and leaves it; a tap elsewhere or Escape closes it.
  pointer("pointerdown", marrow, "touch");
  marrow.click();
  await wait(30);
  check("a tap opens the note at once", note() !== null);
  await wait(500);
  check("a tapped note stays open", note() !== null);
  pointer("pointerdown", document.body, "touch");
  await wait(30);
  check("a tap elsewhere closes it", note() === null);
  pointer("pointerdown", marrow, "touch");
  marrow.click();
  await wait(30);
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  await wait(30);
  check("Escape closes the note", note() === null);
  pointer("pointerdown", marrow, "touch");
  marrow.click();
  await wait(30);
  marrow.click();
  await wait(30);
  check("a second tap on the word closes it", note() === null);

  // "Read more" asks for the help page behind the word.
  const asked: { ns: string; action: string; data: any }[] = [];
  (connection as any).request = async (ns: string, action: string, data: any) => {
    asked.push({ ns, action, data });
    return { kind: "not_found", query: String(data?.query ?? ""), hits: [], suggestions: [] };
  };
  pointer("pointerover", marrow);
  await wait(400);
  note()!.querySelector<HTMLElement>(".more")!.click();
  await wait(30);
  check("Read more closes the note", note() === null);
  // The help store loads on first use, which takes the dev server a moment.
  for (let tries = 0; tries < 60 && asked.length === 0; tries++) await wait(50);
  check(
    "Read more asks for the help page behind the word",
    asked.some((a) => a.ns === "help" && a.action === "help_view" && a.data?.query === tip("saint_marrow").help),
    JSON.stringify(asked),
  );

  // Scrolling moves the word out from under the note, so the note goes.
  pointer("pointerover", marrow);
  await wait(400);
  window.dispatchEvent(new Event("scroll"));
  await wait(30);
  check("scrolling closes the note", note() === null);

  // The setting turns it all off, and the underline with it.
  settings.loreTips = false;
  await wait(50);
  check("the setting off removes the underline", getComputedStyle(marrow).textDecorationLine === "none", getComputedStyle(marrow).textDecorationLine);
  pointer("pointerover", marrow);
  await wait(400);
  check("the setting off opens no note", note() === null);
  settings.loreTips = true;
  await wait(50);
  check("the setting back on brings the underline back", getComputedStyle(marrow).textDecorationLine.includes("underline"));

  // Screen reader mode marks nothing and opens nothing.
  settings.screenreader = true;
  await wait(50);
  check("screen reader mode marks no new line", lore.wrap("to Marrow") === "to Marrow");
  check("screen reader mode removes the underline", getComputedStyle(marrow).textDecorationLine === "none");
  pointer("pointerover", marrow);
  await wait(400);
  check("screen reader mode opens no note", note() === null);
  settings.screenreader = false;

  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__loreTest = { done: true, failures, results };
}

run().catch((error) => {
  failures++;
  results.push(`FAIL harness crashed: ${error}`);
  window.__loreTest = { done: true, failures, results };
});
