// Browser checks for the virtualized game log. Open /tests/log.html on the Vite
// dev server, or run `npm run test:browser`, which drives this page in a real
// Chromium and reads `window.__logTest`.
//
// The log is mounted for real, against the real session store, with no socket:
// lines are appended straight into the scrollback. Every assertion is about
// observable DOM or scroll state - mounted row count, scroll gap, which line is
// at the top of the viewport - never about virtualizer internals.

import { mount, tick } from "svelte";

import GameLog from "../src/components/GameLog.svelte";
import { session } from "../src/lib/session.svelte";
import { logview } from "../src/lib/logview.svelte";
import { settings } from "../src/lib/settings.svelte";
import { screenSize } from "../src/lib/screensize";

declare global {
  interface Window {
    __logTest?: { done: boolean; failures: number; results: string[] };
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

const host = document.getElementById("host")!;
mount(GameLog, { target: host });

const logEl = () => document.querySelector<HTMLElement>(".game-log")!;
const rows = () => Array.from(document.querySelectorAll<HTMLElement>(".log-line"));
const rowCount = () => rows().length;
const gap = () => {
  const el = logEl();
  return el.scrollHeight - el.scrollTop - el.clientHeight;
};
const bodyText = () =>
  rows()
    .map((r) => r.textContent ?? "")
    .join("\n");

/** The first mounted row whose bottom edge is below the viewport's top edge. */
function topRow(): HTMLElement | undefined {
  const top = logEl().getBoundingClientRect().top;
  return rows().find((r) => r.getBoundingClientRect().bottom > top + 0.5);
}
function topRowOffset(): number {
  const row = topRow();
  if (!row) return Number.NaN;
  return row.getBoundingClientRect().top - logEl().getBoundingClientRect().top;
}

/** Let Svelte flush, then a few animation frames for observers and layout. */
async function settle(frames = 4): Promise<void> {
  for (let i = 0; i < frames; i++) {
    await tick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  }
  await tick();
}

function seed(count: number, from: number): void {
  const kinds = ["say", "combat", "look", "text"];
  for (let i = 0; i < count; i++) {
    const n = from + i;
    session.append(`<span>line ${n} marker${n}</span>`, kinds[n % kinds.length]);
  }
}

async function pinToBottom(): Promise<void> {
  const el = logEl();
  el.scrollTop = el.scrollHeight;
  el.dispatchEvent(new Event("scroll"));
  await settle();
}

async function run(): Promise<void> {
  settings.typewriterMs = 0;
  settings.reduceMotion = true;

  session.clear();
  seed(5000, 0);
  await settle();

  check("renders only a window of a 5000-line log", rowCount() > 0 && rowCount() < 80, `${rowCount()} rows`);
  check("opens pinned to the newest line", gap() < 2, `gap ${gap().toFixed(1)}`);
  check("the newest line is mounted", bodyText().includes("line 4999"));

  // An append while following.
  session.append("<span>tail A</span>", "text");
  await settle();
  check("an append while pinned keeps the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);
  check("the appended line is visible", bodyText().includes("tail A"));

  // A burst while following.
  seed(400, 5000);
  await settle();
  check("a burst while pinned keeps the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);
  check("a burst keeps the DOM bounded", rowCount() < 80, `${rowCount()} rows`);

  // Scrolling up: the log must not follow, must count, and must not move.
  const el = logEl();
  el.dispatchEvent(new WheelEvent("wheel", { deltaY: -120, bubbles: true }));
  el.scrollTop -= 400;
  el.dispatchEvent(new Event("scroll"));
  await settle();
  const beforeTop = topRow()?.dataset.lid;
  const beforeOffset = topRowOffset();
  session.append("<span>tail B</span>", "text");
  session.append("<span>tail C</span>", "text");
  await settle();
  const latest = document.querySelector<HTMLElement>(".latest");
  check("scrolling up shows the new-line bar", !!latest && /2 new lines/.test(latest.textContent ?? ""), latest?.textContent ?? "no bar");
  check("an append while scrolled up does not move the viewport", topRow()?.dataset.lid === beforeTop, `${beforeTop} -> ${topRow()?.dataset.lid}`);

  // A trim (the scrollback cap) while scrolled up must anchor on the line the
  // reader is looking at, not on a row number.
  seed(600, 5400);
  await settle();
  check("the trim ran", session.lines.length <= 5500, `${session.lines.length} lines`);
  check("a trim while scrolled up keeps the reading line", topRow()?.dataset.lid === beforeTop, `${beforeTop} -> ${topRow()?.dataset.lid}`);
  check("a trim while scrolled up keeps the pixel offset", Math.abs(topRowOffset() - beforeOffset) < 3, `${beforeOffset.toFixed(1)} -> ${topRowOffset().toFixed(1)}`);

  // Search jumps to the match and marks it.
  logview.search = "marker1234";
  logview.searchOpen = true;
  await settle(6);
  const active = document.querySelector<HTMLElement>(".log-line.active");
  check("search jumps to the match", !!active && (active.textContent ?? "").includes("marker1234"), active?.textContent ?? "no active row");
  logview.searchOpen = false;
  logview.search = "";
  await settle();

  await pinToBottom();

  // Category filters re-index the list; no row of a hidden category may render.
  logview.toggle("combat");
  await settle();
  check("filtering hides a category", rows().every((r) => r.dataset.cat !== "combat"));
  check("filtering keeps the DOM bounded", rowCount() < 80, `${rowCount()} rows`);
  check("filtering keeps the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);
  logview.toggle("combat");
  await settle();

  // Timestamps change row content under the virtualizer's measurements.
  logview.timestamps = true;
  await settle();
  check("timestamps render on mounted rows", document.querySelectorAll(".log-line .ts").length > 0);
  check("timestamps keep the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);
  logview.timestamps = false;
  await settle();

  // Fast scrolling across the whole buffer remounts rows constantly; the DOM
  // must stay a window, not grow toward the scrollback.
  el.scrollTop = 0;
  el.dispatchEvent(new Event("scroll"));
  await settle(2);
  el.scrollTop = el.scrollHeight / 2;
  el.dispatchEvent(new Event("scroll"));
  await settle(2);
  el.scrollTop = el.scrollHeight;
  el.dispatchEvent(new Event("scroll"));
  await settle(2);
  check("fast scrolling keeps the DOM bounded", rowCount() < 80, `${rowCount()} rows`);

  await pinToBottom();

  // A fresh line types in; an old one (scrolled back to) does not.
  settings.reduceMotion = false;
  settings.typewriterMs = 200;
  session.append(`<span>${"typewriter target ".repeat(10)}</span>`, "text");
  await settle(1);
  const typingRow = rows().at(-1);
  const typingNow = typingRow?.querySelector(".body")?.textContent ?? "";
  check("a fresh line starts partially revealed", typingNow.length < 200, `${typingNow.length} chars`);
  await new Promise((resolve) => setTimeout(resolve, 500));
  await settle();
  const typed = rows().at(-1)?.querySelector(".body")?.textContent ?? "";
  check("the typewriter finishes the line", typed === "typewriter target ".repeat(10), `${typed.length} chars`);
  settings.typewriterMs = 0;
  settings.reduceMotion = true;

  // A hidden panel (dockview hides rather than destroys) must come back where
  // it was.
  host.style.display = "none";
  await settle();
  host.style.display = "";
  await settle(6);
  check("a restored panel returns to the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);

  el.dispatchEvent(new WheelEvent("wheel", { deltaY: -120, bubbles: true }));
  el.scrollTop -= 400;
  el.dispatchEvent(new Event("scroll"));
  await settle();
  const hiddenTop = topRow()?.dataset.lid;
  host.style.display = "none";
  await settle();
  host.style.display = "";
  await settle(6);
  check("a restored panel returns to the reading line", topRow()?.dataset.lid === hiddenTop, `${hiddenTop} -> ${topRow()?.dataset.lid}`);

  await pinToBottom();

  // Media rows: the image's height is reserved, then confirmed by layout.
  const svg = encodeURIComponent("<svg xmlns='http://www.w3.org/2000/svg' width='120' height='80'></svg>");
  session.append(`<img alt="media" width="120" height="80" src="data:image/svg+xml,${svg}">`, "media");
  await settle(6);
  check("a media row keeps the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);

  // Real traffic. Every server message is its own socket frame, so each append
  // runs in its own task and several land inside one animation frame, before
  // the browser has delivered the scroll event for the previous follow. Lines
  // wrap to several rows, and the typewriter (on by default) grows each row as
  // it types. The log has to be at the bottom when the traffic stops.
  await pinToBottom();
  settings.reduceMotion = false;
  settings.typewriterMs = 275;
  for (let i = 0; i < 12; i++) {
    session.append(`<span>${"traffic ".repeat(40)}${i}</span><br><span>second row ${i}</span>`, "text");
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  await new Promise((resolve) => setTimeout(resolve, 700));
  await settle(6);
  check("socket-frame bursts keep the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);
  check("socket-frame bursts show the newest line", bodyText().includes("second row 11"));

  // Paced traffic: one wrapped line every few frames while the previous one is
  // still typing in.
  for (let i = 0; i < 8; i++) {
    session.append(`<span>${"paced ".repeat(50)}${i}</span>`, "text");
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
  await new Promise((resolve) => setTimeout(resolve, 700));
  await settle(6);
  check("paced wrapped lines keep the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);

  // The same traffic must not pull a reader who has scrolled up back down,
  // whether they left with the wheel or by dragging the scrollbar (no wheel).
  for (const how of ["wheel", "drag"]) {
    await pinToBottom();
    if (how === "wheel") el.dispatchEvent(new WheelEvent("wheel", { deltaY: -120, bubbles: true }));
    el.scrollTop -= 600;
    el.dispatchEvent(new Event("scroll"));
    await settle();
    const readingLine = topRow()?.dataset.lid;
    for (let i = 0; i < 6; i++) {
      session.append(`<span>${"reader ".repeat(40)}${i}</span>`, "text");
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
    await settle(6);
    check(`socket-frame traffic leaves a reader who scrolled up (${how}) in place`, topRow()?.dataset.lid === readingLine, `${readingLine} -> ${topRow()?.dataset.lid}`);
  }
  settings.typewriterMs = 0;
  settings.reduceMotion = true;
  await settle();

  // Typing must not cost a second click: a mouse click in the terminal hands
  // the keyboard back to the command line.
  const cmd = document.getElementById("cmd") as HTMLInputElement;
  const click = (target: Element) =>
    target.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));

  cmd.focus();
  logEl().focus(); // a real click on a row lands focus on the scroll box
  click(rows()[2]);
  await tick();
  check(
    "clicking a log row returns focus to the command line",
    document.activeElement === cmd,
    document.activeElement?.className || document.activeElement?.id || "none",
  );

  const chip = document.querySelector<HTMLElement>(".chip")!;
  chip.focus();
  click(chip);
  await tick();
  check(
    "clicking a filter chip returns focus to the command line",
    document.activeElement === cmd,
    document.activeElement?.className || "none",
  );
  click(chip); // restore the filter
  await settle();

  // A drag-select keeps its selection and its focus.
  const selectRow = rows()[2];
  const range = document.createRange();
  range.selectNodeContents(selectRow.querySelector(".body")!);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  logEl().focus();
  click(selectRow);
  await tick();
  check("a text selection is not stolen by the command line", document.activeElement !== cmd);
  selection.removeAllRanges();

  // A control activated from the keyboard keeps focus.
  chip.focus();
  chip.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 }));
  await tick();
  check("keyboard activation keeps its focus", document.activeElement === chip);
  cmd.focus();

  // The size reported to the server is the characters that really fit: the
  // server wraps and lays out tables to it, so a column too many breaks every
  // full line in two and a column too few wastes the edge.
  const fitsPerLine = (): number => {
    const row = rows().find((r) => (r.textContent ?? "").includes("x".repeat(40)));
    const body = row?.querySelector<HTMLElement>(".body");
    const text = body ? (document.createTreeWalker(body, NodeFilter.SHOW_TEXT).nextNode() as Text | null) : null;
    if (!text) return -1;
    const range = document.createRange();
    let firstTop: number | null = null;
    for (let i = 0; i < text.length; i++) {
      range.setStart(text, i);
      range.setEnd(text, i + 1);
      const top = range.getBoundingClientRect().top;
      if (firstTop === null) firstTop = top;
      else if (top > firstTop + 2) return i;
    }
    return text.length;
  };
  logview.timestamps = false;
  session.append(`<span>${"x".repeat(600)}</span>`, "text");
  await settle();
  logEl().scrollTop = logEl().scrollHeight;
  await settle(8);
  check("reports the columns that fit", screenSize.current?.cols === fitsPerLine(), `${screenSize.current?.cols} vs ${fitsPerLine()}`);
  host.style.width = "400px";
  await settle(8);
  check("a narrower log reports fewer columns", screenSize.current?.cols === fitsPerLine() && fitsPerLine() < 50, `${screenSize.current?.cols} vs ${fitsPerLine()}`);
  logview.timestamps = true;
  await settle(8);
  check("timestamps take their width off the report", screenSize.current?.cols === fitsPerLine(), `${screenSize.current?.cols} vs ${fitsPerLine()}`);
  logview.timestamps = false;
  host.style.width = "";
  await settle(8);
  check("the report follows the log back", screenSize.current?.cols === fitsPerLine(), `${screenSize.current?.cols} vs ${fitsPerLine()}`);

  // Clear empties the scrollback and the DOM.
  session.clear();
  await settle();
  check("clear empties the log", rowCount() === 0 && session.lines.length === 0, `${rowCount()} rows`);

  check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));

  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__logTest = { done: true, failures, results };
}

run().catch((error) => {
  failures++;
  results.push(`FAIL harness crashed: ${error}`);
  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__logTest = { done: true, failures, results };
});
