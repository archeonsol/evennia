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

  // Selection and copy (lib/logcopy.ts). A selection is DOM nodes and the log
  // mounts only a window of rows: the rows a selection starts and ends on must
  // stay mounted wherever the reader scrolls, and a copy across lines must
  // come from the scrollback, not from whichever rows happen to be mounted.
  // Every category on: the checks above leave a chip toggled.
  logview.reset();
  session.clear();
  await settle();
  seed(3000, 0);
  // A line with a break in it, and one with a palette colour and a link that
  // only works inside the client: how the text reads and how the HTML travels.
  session.append("<span>first half</span><br><span>second half</span>", "text");
  session.append('<span class="color-196">red alert</span> and <a id="mxplink" href="#" onclick="return false">a link</a>', "text");
  seed(200, 3002);
  await settle();
  await pinToBottom();

  const sel = window.getSelection()!;
  const byText = (text: string) => rows().find((r) => r.querySelector(".body")?.textContent === text);
  const firstText = (row: HTMLElement) =>
    document.createTreeWalker(row.querySelector(".body")!, NodeFilter.SHOW_TEXT).nextNode() as Text;
  const mounted = (lid: number) => !!document.querySelector(`.log-line[data-lid="${lid}"]`);
  const idOf = (text: string) => session.lines.find((l) => l.text === text)?.id ?? -1;
  const indexOf = (text: string) => session.lines.findIndex((l) => l.text === text);
  const copy = () => {
    const data = new DataTransfer();
    const event = new ClipboardEvent("copy", { clipboardData: data, bubbles: true, cancelable: true });
    logEl().dispatchEvent(event);
    return { event, data };
  };
  const scrollTo = async (top: number) => {
    el.dispatchEvent(new WheelEvent("wheel", { deltaY: -120, bubbles: true }));
    el.scrollTop = top;
    el.dispatchEvent(new Event("scroll"));
    await settle();
  };

  await scrollTo(0);
  const row5 = byText("line 5 marker5")!;
  const row7 = byText("line 7 marker7")!;
  sel.setBaseAndExtent(firstText(row5), 3, firstText(row7), 4);
  await settle();
  const lid5 = idOf("line 5 marker5");
  const lid7 = idOf("line 7 marker7");
  await scrollTo(el.scrollHeight);
  check("a selection's first line stays mounted when the reader scrolls away", mounted(lid5));
  check("a selection's last line stays mounted when the reader scrolls away", mounted(lid7));
  check("held rows keep the DOM bounded", rowCount() < 84, `${rowCount()} rows`);
  check(
    "the selection survives the scroll",
    sel.anchorNode?.isConnected === true && sel.toString().startsWith("e 5 marker5"),
    JSON.stringify(sel.toString().slice(0, 40)),
  );

  // Extend it to a line near the bottom: three thousand lines, most unmounted.
  const rowEnd = byText("line 3195 marker3195")!;
  sel.extend(firstText(rowEnd), 6);
  await settle();
  check("extending keeps the start held", mounted(lid5));
  check("extending releases the old end", !mounted(lid7));

  // Rows that mount between the ends sit between them in the DOM, so the
  // browser paints them selected.
  await scrollTo(el.scrollHeight / 2);
  const middle = rows().find((r) => {
    const lid = Number(r.dataset.lid);
    return lid > lid5 + 50 && lid < idOf("line 3195 marker3195") - 50;
  });
  check("a row scrolled into the middle of a selection is selected", !!middle && sel.containsNode(middle.querySelector(".body")!, true), middle?.textContent ?? "no middle row");

  const expected = session.lines
    .slice(indexOf("line 5 marker5"), indexOf("line 3195 marker3195") + 1)
    .map((l) => l.text);
  expected[0] = expected[0].slice(3);
  expected[expected.length - 1] = expected[expected.length - 1].slice(0, 6);
  const started = performance.now();
  const across = copy();
  const copyMs = performance.now() - started;
  const text = across.data.getData("text/plain");
  check("a copy across lines is built from the scrollback", across.event.defaultPrevented);
  check("the copy holds every line in order, cut at both ends", text === expected.join("\n"), `${text.split("\n").length} lines vs ${expected.length}; starts ${JSON.stringify(text.slice(0, 30))}`);
  check("a line with a break copies as two lines", text.includes("first half\nsecond half"));
  check("a copy of three thousand lines is quick", copyMs < 1000, `${copyMs.toFixed(0)} ms`);

  const html = across.data.getData("text/html");
  const block = new DOMParser().parseFromString(html, "text/html").body.firstElementChild as HTMLElement | null;
  const red = Array.from(block?.querySelectorAll("span") ?? []).find((s) => s.textContent === "red alert");
  check("the HTML copy has one block per line", block?.children.length === expected.length, `${block?.children.length} blocks`);
  check("the HTML copy carries the log's background", !!block?.style.backgroundColor);
  check("a palette colour travels inline", red?.style.color === "rgb(255, 0, 0)", red?.getAttribute("style") ?? "no span");
  check("a client-only link pastes as its text", !block?.querySelector("a") && html.includes("a link"));
  check("no handler or class travels", !/onclick|class=|id=/i.test(html));

  // A selection inside one line, or one that leaves the log, is the browser's.
  await pinToBottom();
  const single = byText("line 3195 marker3195")!;
  sel.setBaseAndExtent(firstText(single), 0, firstText(single), 4);
  await settle();
  check("a copy inside one line is left to the browser", !copy().event.defaultPrevented);
  const report = document.getElementById("results")!;
  sel.setBaseAndExtent(firstText(single), 2, report.firstChild ?? report, 0);
  await settle();
  check("a copy reaching outside the log is left to the browser", !copy().event.defaultPrevented);

  // Even a selection inside one line is held; clearing it lets the row go.
  await scrollTo(0);
  const top5 = byText("line 5 marker5")!;
  sel.setBaseAndExtent(firstText(top5), 0, firstText(top5), 4);
  await settle();
  await pinToBottom();
  check("a selection inside one line is held too", mounted(lid5));
  sel.removeAllRanges();
  await settle();
  check("clearing the selection releases the held rows", !mounted(lid5) && rowCount() < 80, `${rowCount()} rows`);

  // Select all in the log is the whole scrollback, including a line that is
  // still typing in: its end is the end of the line, not of what has typed.
  settings.reduceMotion = false;
  settings.typewriterMs = 400;
  const typing = "still typing ".repeat(8).trim();
  session.append(`<span>${typing}</span>`, "text");
  await settle(1);
  logEl().focus();
  logEl().dispatchEvent(new KeyboardEvent("keydown", { key: "a", code: "KeyA", ctrlKey: true, bubbles: true, cancelable: true }));
  await settle(3);
  check("select all holds the first and the last line", mounted(session.lines[0].id) && mounted(session.lines.at(-1)!.id));
  const all = copy();
  const allText = all.data.getData("text/plain");
  check(
    "select all copies the whole scrollback",
    all.event.defaultPrevented && allText === session.lines.map((l) => l.text).join("\n"),
    `${allText.split("\n").length} lines vs ${session.lines.length}; ends ${JSON.stringify(allText.slice(-30))}`,
  );
  check("a line still typing in copies whole", allText.endsWith(typing));
  sel.removeAllRanges();
  settings.typewriterMs = 0;
  settings.reduceMotion = true;
  await settle();
  check("the DOM is a window again after select all", rowCount() < 80, `${rowCount()} rows`);

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
