// Browser checks for the command line and the compose pad. Open
// /tests/input.html on the Vite dev server, or run `npm run test:browser`.
//
// The real CommandInput is mounted against the real command, compose and
// settings stores, with the socket stubbed: sent lines are recorded instead.
// Keys are dispatched as the browser would; a synthetic key never inserts
// text or moves the caret, so a check that a key was left to the browser
// looks at whether it was cancelled.

import { mount, tick } from "svelte";

import "../src/styles/themes.css";
import "../src/styles/shell.css";
import CommandInput from "../src/components/CommandInput.svelte";
import { announcer } from "../src/lib/announce.svelte";
import { commands } from "../src/lib/commands.svelte";
import { compose } from "../src/lib/compose.svelte";
import { connection } from "../src/lib/evennia.svelte";
import { settings } from "../src/lib/settings.svelte";
import { caretOnEdge } from "../src/lib/textarea";

declare global {
  interface Window {
    __inputTest?: { done: boolean; failures: number; results: string[] };
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

const sent: string[] = [];
(connection as any).state = "open";
connection.sendCommand = (line: string) => {
  sent.push(line);
};
// Tab-completion warms its cache as words are typed; answer with nothing.
(connection as any).request = async () => ({ names: [] });

// A clean slate: nothing persisted from an earlier run of this page. History
// is recorded only for a signed-in account.
commands.useAccount(1);
compose.useAccount(1);
commands.recent = [];
compose.setText("");
compose.setMode("pose");
settings.keepCommand = false;
settings.historyKeys = "edge";
settings.composeStaysOpen = false;

const host = document.getElementById("host")!;
mount(CommandInput, { target: host });

const line = () => host.querySelector<HTMLTextAreaElement>("textarea.command-input")!;
const pad = () => host.querySelector<HTMLTextAreaElement>(".compose textarea");
const lastSaid = () => announcer.polite.at(-1)?.lines.join(" ") ?? "";

/** Type into a field: set its text, caret at the end unless told otherwise. */
async function type(el: HTMLTextAreaElement | HTMLInputElement, text: string, caret = text.length): Promise<void> {
  el.focus();
  el.value = text;
  el.setSelectionRange(caret, caret);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  await settle();
}

/** Press a key on an element. Returns whether the shell took it (cancelled it). */
async function press(el: Element, key: string, mods: KeyboardEventInit = {}): Promise<boolean> {
  const ev = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...mods });
  el.dispatchEvent(ev);
  await settle();
  return ev.defaultPrevented;
}

/** The line break Enter asks the browser for. Returns whether the shell took it. */
async function lineBreak(el: Element): Promise<boolean> {
  const ev = new InputEvent("beforeinput", { inputType: "insertLineBreak", bubbles: true, cancelable: true });
  el.dispatchEvent(ev);
  await settle();
  return ev.defaultPrevented;
}

function paste(el: HTMLElement, text: string): void {
  const data = new DataTransfer();
  data.setData("text", text);
  el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
}

async function run(): Promise<void> {
  await settle();

  // -- the field ------------------------------------------------------------
  const el = line();
  check("the command line is a textarea", el?.tagName === "TEXTAREA", el?.tagName ?? "none");
  check("it starts one row high", el?.rows === 1);
  check("mobile keyboards label Enter as send", el?.getAttribute("enterkeyhint") === "send");
  const hint = document.getElementById(el?.getAttribute("aria-describedby") ?? "");
  check("a reader is told what Enter does", !!hint && /Shift\+Enter/.test(hint.textContent ?? ""), hint?.textContent ?? "none");
  check("the field is focused on mount", document.activeElement === el);
  const oneRow = el.offsetHeight;

  // -- Up and Down keep the typed line --------------------------------------
  commands.recent = ["say three", "say two", "say one"];
  await type(el, "half a pose");
  check("Up is taken", await press(el, "ArrowUp"));
  check("Up recalls the newest command", el.value === "say three", el.value);
  check("the recalled command is spoken", lastSaid() === "say three", lastSaid());
  await press(el, "ArrowUp");
  check("Up again goes older", el.value === "say two", el.value);
  await press(el, "ArrowDown");
  check("Down goes newer", el.value === "say three", el.value);
  check("Down past the newest is taken", await press(el, "ArrowDown"));
  check("Down past the newest brings the typed line back", el.value === "half a pose", el.value);
  check("Down on the typed line is left to the browser", !(await press(el, "ArrowDown")));
  check("Down on the typed line keeps it", el.value === "half a pose", el.value);

  // An edit to a recalled command survives walking past it.
  await press(el, "ArrowUp");
  await type(el, "say three!");
  await press(el, "ArrowUp");
  check("walking on from an edit", el.value === "say two", el.value);
  await press(el, "ArrowDown");
  check("coming back finds the edit", el.value === "say three!", el.value);

  // Sending a recalled command puts the typed line back.
  check("Enter is taken", await press(el, "Enter"));
  check("the edited command is sent", sent.at(-1) === "say three!", String(sent.at(-1)));
  check("the typed line comes back after the send", el.value === "half a pose", el.value);
  check("its return is spoken", lastSaid() === "Restored: half a pose", lastSaid());
  await press(el, "Enter");
  check("the typed line itself sends", sent.at(-1) === "half a pose", String(sent.at(-1)));
  check("and the line is then empty", el.value === "", el.value);

  // -- Enter, Shift+Enter, IME ---------------------------------------------
  const before = sent.length;
  await type(el, "line one");
  check("Shift+Enter is left to the browser (a new line)", !(await press(el, "Enter", { shiftKey: true })));
  check("the line break it asks for is let through", !(await lineBreak(el)));
  check("Shift+Enter sends nothing", sent.length === before);
  check("Enter mid-composition is left to the IME", !(await press(el, "Enter", { isComposing: true })));
  check("Enter confirming Safari's IME is left to it", !(await press(el, "Enter", { keyCode: 229 })));
  check("Enter mid-composition sends nothing", sent.length === before);
  await type(el, "line one\nline two");
  await press(el, "Enter");
  check("a multi-line command goes as one line", sent.length === before + 1 && sent.at(-1) === "line one\nline two", JSON.stringify(sent.slice(before)));

  // A phone keyboard's Enter can reach the page only as a line break.
  await type(el, "look");
  check("a bare line break is taken", await lineBreak(el));
  check("and sends the line", sent.at(-1) === "look" && el.value === "", `${sent.at(-1)} / ${JSON.stringify(el.value)}`);

  // -- a long line grows the field, and the arrows move through it ----------
  commands.recent = ["line one\nline two", "half a pose", "say three"];
  const long = "a long pose that wraps across the command line ".repeat(6).trim();
  await type(el, long);
  check("a long line grows the field", el.offsetHeight > oneRow * 2, `${el.offsetHeight} vs ${oneRow}`);
  check("all of it shows", el.scrollHeight <= el.clientHeight + 1, `${el.scrollHeight} > ${el.clientHeight}`);
  check("the caret at the end is on the last line", caretOnEdge(el, "last") && !caretOnEdge(el, "first"));
  check("Up from a lower line moves the caret, not the history", !(await press(el, "ArrowUp")));
  check("and keeps the line", el.value === long);
  el.setSelectionRange(Math.floor(long.length / 2), Math.floor(long.length / 2));
  check("a middle line is neither edge", !caretOnEdge(el, "first") && !caretOnEdge(el, "last"));
  el.setSelectionRange(5, 5);
  check("the caret near the start is on the first line", caretOnEdge(el, "first") && !caretOnEdge(el, "last"));
  check("Up from the first line recalls", await press(el, "ArrowUp"));
  check("the newest command shows", el.value === "line one\nline two", JSON.stringify(el.value));
  // An untouched recalled command keeps walking from anywhere in it: the
  // caret lands at its end, below its first line.
  await press(el, "ArrowUp");
  check("Up walks on through an untouched multi-line command", el.value === "half a pose", el.value);
  await press(el, "ArrowDown");
  await press(el, "ArrowDown");
  check("Down walks back to the long typed line", el.value === long);
  await type(el, "");
  check("an empty line shrinks back to one row", el.offsetHeight === oneRow, `${el.offsetHeight} vs ${oneRow}`);
  await type(el, "word ".repeat(2000));
  check("a huge line stops growing and scrolls", el.scrollHeight > el.clientHeight + 1 && el.offsetHeight <= innerHeight * 0.4 + 1, `${el.offsetHeight}px`);
  await type(el, "");

  // Hard line breaks settle the edges without measuring.
  await type(el, "a\nb", 1);
  check("before a line break is the first line only", caretOnEdge(el, "first") && !caretOnEdge(el, "last"));
  el.setSelectionRange(3, 3);
  check("after it is the last line only", caretOnEdge(el, "last") && !caretOnEdge(el, "first"));
  await type(el, "");

  // -- the "only from an empty line" setting --------------------------------
  commands.recent = ["say three", "say two", "say one"];
  settings.historyKeys = "empty";
  await type(el, "typed");
  check('"empty": Up on typed text is left to the browser', !(await press(el, "ArrowUp")));
  check('"empty": the typed text stays', el.value === "typed");
  await type(el, "");
  check('"empty": Up on an empty line recalls', await press(el, "ArrowUp"));
  await type(el, `${el.value} again`);
  check('"empty": an edited recalled command still walks', await press(el, "ArrowUp"));
  await press(el, "ArrowDown");
  await press(el, "ArrowDown");
  settings.historyKeys = "edge";
  await type(el, "");

  // -- "Keep command after sending" -----------------------------------------
  settings.keepCommand = true;
  await type(el, "look");
  await press(el, "Enter");
  check("a kept command stays on the line", el.value === "look");
  await press(el, "ArrowUp");
  check("Up from a kept command skips the same command", el.value !== "look" && el.value === commands.recent[1], el.value);
  await press(el, "ArrowDown");
  settings.keepCommand = false;
  await type(el, "");

  // -- Ctrl+R keeps the typed line too ---------------------------------------
  commands.recent = ["say three", "say two", "say one"];
  await type(el, "draft text");
  await press(el, "r", { ctrlKey: true });
  const search = host.querySelector<HTMLInputElement>(".rs-input");
  check("Ctrl+R opens the history search", !!search);
  if (search) {
    await type(search, "two");
    await press(search, "Enter");
  }
  const back = line();
  check("the match lands on the line", back?.value === "say two", back?.value ?? "none");
  await press(back, "ArrowDown");
  await press(back, "ArrowDown");
  check("walking down from a match finds the typed line", back.value === "draft text", back.value);
  await type(back, "");

  // -- paste --------------------------------------------------------------
  const realConfirm = window.confirm;
  await type(back, "say ");
  paste(back, "hello\n");
  await settle();
  check("a line pasted with its line break goes in without it", back.value === "say hello", JSON.stringify(back.value));
  const pasted = sent.length;
  window.confirm = () => true;
  paste(back, "look\nwho\n");
  await settle();
  check("pasted lines can go as separate commands", sent.slice(pasted).join("|") === "look|who", sent.slice(pasted).join("|"));
  check("sending them keeps what was typed", back.value === "say hello", back.value);
  window.confirm = () => false;
  await type(back, "");
  paste(back, "first paragraph\r\n\r\nsecond paragraph\r\n");
  await settle();
  check("or be pasted into the line, breaks and all", back.value === "first paragraph\n\nsecond paragraph", JSON.stringify(back.value));
  check("that paste sends nothing", sent.length === pasted + 2);
  window.confirm = realConfirm;
  await type(back, "");

  // -- the compose pad -----------------------------------------------------
  const composeBtn = () => host.querySelector<HTMLButtonElement>(".compose-btn")!;
  await type(back, "a half-typed pose");
  composeBtn().click();
  await settle();
  check("Compose carries the typed line into the pad", pad()?.value === "a half-typed pose", pad()?.value ?? "none");
  check("and takes it off the command line", line().value === "", JSON.stringify(line().value));
  check("the pad takes the keys", document.activeElement === pad());
  await press(pad()!, "Escape");
  check("Esc closes the pad", !pad());
  check("Esc keeps the draft", compose.text === "a half-typed pose");
  check("the keys go back to the command line", document.activeElement === line());

  await type(line(), "look at the bar");
  composeBtn().click();
  await settle();
  check("a saved draft is not overwritten by the line", pad()?.value === "a half-typed pose", pad()?.value ?? "none");
  check("the line then stays where it is", line().value === "look at the bar", line().value);

  await press(pad()!, "Enter", { ctrlKey: true });
  check("Ctrl+Enter sends the pose", sent.at(-1) === ".a half-typed pose", String(sent.at(-1)));
  check("the pad closes after sending by default", !pad());
  check("sending from the pad leaves the command line alone", line().value === "look at the bar", line().value);
  check("the keys go back to the command line after a send", document.activeElement === line());

  await type(line(), "");
  composeBtn().click();
  await settle();
  const keep = host.querySelector<HTMLButtonElement>(".c-foot .sh-toggle")!;
  check("the pad offers to stay open", keep?.getAttribute("aria-pressed") === "false", keep?.outerHTML ?? "none");
  keep.click();
  await settle();
  check("that choice is the setting", settings.composeStaysOpen === true);
  check("the switch shows it", keep.getAttribute("aria-pressed") === "true");
  compose.setMode("emote");
  await type(pad()!, "waves.");
  host.querySelector<HTMLButtonElement>(".c-send")!.click();
  await settle();
  check("Send sends the emote", sent.at(-1) === "emote waves.", String(sent.at(-1)));
  check("kept open, the pad stays", !!pad());
  check("kept open, the pad is empty", pad()?.value === "", pad()?.value ?? "none");
  check("kept open, the mode stays", compose.mode === "emote");
  check("kept open, the pad keeps the keys", document.activeElement === pad());
  const count = sent.length;
  host.querySelector<HTMLButtonElement>(".c-send")!.click();
  await settle();
  check("an empty pad sends nothing", sent.length === count);
  check("and stays open", !!pad());

  check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));

  // Leave a readable state on screen for a screenshot: the pad open over a
  // command line holding a long pose.
  compose.setMode("pose");
  await type(pad()!, "leans on the bar, ");
  await type(line(), long);

  const pre = document.getElementById("results")!;
  pre.textContent = results.join("\n");
  window.__inputTest = { done: true, failures, results };
}

void run();
