// Browser checks for the game screen panel. Run with `npm run test:browser -- screen`.
//
// The real panel, store and xterm run against a stubbed connection. The page plays the Server: it
// opens a screen, draws a fake program on it, and answers nothing but records every action the panel
// sends, so the checks can say what a key press did.

import { mount, tick } from "svelte";

import "../src/styles/themes.css";
import "../src/styles/shell.css";
import "../src/styles/ansi-palette.css";
import ScreenPanel from "../src/components/ScreenPanel.svelte";
import { screens } from "../src/lib/screens.svelte";

declare global {
  interface Window {
    __screenTest?: { done: boolean; failures: number; results: string[] };
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

const ID = "arcade-test";
const sent: { action: string; kwargs: Record<string, any> }[] = [];
// For the runner, which presses real keys from outside the page and reads what they sent.
(window as any).__screenSent = sent;
(window as any).__screens = screens;
const closed: string[] = [];
const said: string[] = [];
screens.connect({
  send: (action, kwargs) => sent.push({ action, kwargs }),
  openPanel: () => {},
  closePanel: (id) => closed.push(id),
  say: (text) => said.push(text),
});

const ESC = String.fromCharCode(27);
const at = (row: number, col: number, text: string, sgr = "0") => `${ESC}[${row};${col}H${ESC}[${sgr}m${text}${ESC}[0m`;

// What nInvaders itself draws at level 1 (view.c and aliens.c of the real program): three letter
// aliens on every other line, a bunker row, the gun, a missile in the air and the status line on the
// last row, in red.
const RED = "31";
const GREEN = "32";
const YELLOW = "33";
const BLUE = "34";
const MAGENTA = "35";
const CYAN = "36";
const WHITE = "37";
const BUNKERS = [
  "        ###                 ###                 ###                 ###         ",
  "       #####               #####               #####               #####        ",
  "      #######             #######             #######             #######       ",
  "      ##   ##             ##   ##             ##   ##             ##   ##       ",
];
function program(score: number): string {
  let out = `${ESC}[2J${ESC}[?25l`;
  out += at(1, 44, "<o o>", MAGENTA);
  const rows: [string, string, number][] = [
    [",^,", RED, 10],
    ["_O-", GREEN, 10],
    ["_O-", GREEN, 10],
    ["-o-", BLUE, 10],
    ["-o-", BLUE, 8],
  ];
  rows.forEach(([ship, colour, count], n) => {
    out += at(3 + n * 2, 14, ship.repeat(count), colour);
  });
  BUNKERS.forEach((line, n) => {
    out += at(17 + n, 1, line.replace(/ +$/, ""), CYAN);
  });
  out += at(13, 31, ":", CYAN);
  out += at(15, 39, "!", WHITE);
  out += at(23, 37, "/-^-\\", YELLOW);
  out += at(24, 17, `Level: 01 Score: ${String(score).padStart(7, "0")} Lives: /-\\ /-\\ /-\\ `, RED);
  return out;
}

const root = document.getElementById("panel")!;
const text = () => root.querySelector(".xterm-rows")?.textContent ?? "";
const $ = <T extends Element = HTMLElement>(sel: string) => root.querySelector<T>(sel);
const $$ = (sel: string) => Array.from(root.querySelectorAll<HTMLElement>(sel));
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const scaleOf = () => {
  const m = getComputedStyle($(".host")!).transform.match(/matrix\(([^,]+)/);
  return m ? Number.parseFloat(m[1]) : NaN;
};

function press(key: string, code: string, keyCode: number, init: KeyboardEventInit = {}): void {
  const area = $<HTMLTextAreaElement>("textarea.xterm-helper-textarea");
  area?.dispatchEvent(
    new KeyboardEvent("keydown", { key, code, keyCode, which: keyCode, bubbles: true, cancelable: true, ...init } as KeyboardEventInit),
  );
}
const keysSent = () => sent.filter((s) => s.action === "screen_key").map((s) => s.kwargs.data as string);

async function run(): Promise<void> {
  screens.open({
    id: ID,
    type: "screen",
    mode: "terminal",
    title: "Invaders",
    size: [80, 24],
    controls: [["Left, Right", "move the gun"], ["Space", "fire"], ["P", "pause"], ["Q", "quit"]],
    dismissible: true,
  });
  mount(ScreenPanel, { target: root, props: { screenId: ID } });
  await settle(6);
  await wait(60);
  await settle();

  // A colour as the shell's own tokens give it, so a check can say a part uses one.
  const token = (name: string) => {
    const probe = document.createElement("i");
    probe.style.color = `var(${name})`;
    document.body.append(probe);
    const colour = getComputedStyle(probe).color;
    probe.remove();
    return colour;
  };

  check("the name shows in the head", $(".head .name")?.textContent === "Invaders", $(".head .name")?.textContent ?? "");
  check("the screen is as big as the program says", $$(".xterm-rows > div").length === 24, String($$(".xterm-rows > div").length));
  check("the screen asks to be drawn when it appears", sent.some((s) => s.action === "screen_sync" && s.kwargs.id === ID), JSON.stringify(sent));
  check("the panel lists no keys of its own", $$(".keys, kbd, footer").length === 0, String($$(".keys, kbd, footer").length));
  check("a screen reader is told the keys instead", said.length === 1 && said[0].includes("Left, Right, move the gun.") && said[0].includes("Shift and Escape"), JSON.stringify(said));
  check("the opening was said once", said.length === 1 && said[0].includes("Invaders"), JSON.stringify(said));
  check("the head has one command, to close", $$(".head .sh-cmd").length === 1 && $(".head .sh-cmd")?.textContent === "Close");
  check("the state says the keys go to the game", $(".state")?.textContent?.trim() === "Playing", $(".state")?.textContent ?? "");
  check("the border lights while the keys go to the game", getComputedStyle($(".tile")!).borderTopColor === token("--accent-bright"), getComputedStyle($(".tile")!).borderTopColor);
  check("the head sits on the shell's accent rule", getComputedStyle($(".head")!).borderBottomColor === token("--accent"));
  check("the screen shows the well's ground, which is the shell's", getComputedStyle($(".well")!).backgroundColor === token("--bg-deep") && getComputedStyle($(".xterm-viewport")!).backgroundColor === "rgba(0, 0, 0, 0)", `${getComputedStyle($(".well")!).backgroundColor} / ${getComputedStyle($(".xterm-viewport")!).backgroundColor}`);
  const glowing = $$("*").filter((el) => getComputedStyle(el).boxShadow !== "none" || getComputedStyle(el).textShadow !== "none");
  check("nothing in the panel glows or casts a shadow", glowing.length === 0, glowing.map((el) => el.className || el.tagName).join(","));

  // What the program prints lands on the screen.
  screens.frame(ID, program(1250));
  await settle(4);
  check("output is drawn", text().includes("Score: 0001250"), text().slice(0, 120));
  check("a colour reaches the screen", $$(".xterm-rows span").some((s) => /xterm-fg-1\b|xterm-fg-2\b|xterm-fg-3\b/.test(s.className)));
  screens.frame(ID, at(24, 17, "Level: 01 Score: 0001300 Lives: /-\\ /-\\ ", RED));
  await settle(3);
  check("a later frame draws over it", text().includes("Score: 0001300"));

  // The letters were measured and the picture was fitted into the space.
  const fit = scaleOf();
  check("the screen is scaled to fit", Number.isFinite(fit) && fit > 0.3, String(fit));
  const stage = $(".stage")!.getBoundingClientRect();
  const well = $(".well")!.getBoundingClientRect();
  check("the screen fits its space", stage.width <= well.width && stage.height <= well.height, `${stage.width}x${stage.height} in ${well.width}x${well.height}`);
  const roomy = fit;
  root.style.width = "420px";
  root.style.height = "400px";
  await settle(6);
  const tight = scaleOf();
  check("a smaller panel scales the screen down", tight < roomy, `${tight} vs ${roomy}`);
  const small = $(".stage")!.getBoundingClientRect();
  const smallWell = $(".well")!.getBoundingClientRect();
  check("and it still fits", small.width <= smallWell.width && small.height <= smallWell.height, `${small.width}x${small.height} in ${smallWell.width}x${smallWell.height}`);
  root.style.width = "760px";
  root.style.height = "580px";
  await settle(6);
  check("a bigger panel scales it back up", Math.abs(scaleOf() - roomy) < 0.02, `${scaleOf()} vs ${roomy}`);

  // Keys go to the Server as the terminal made them.
  const focus = $<HTMLTextAreaElement>("textarea.xterm-helper-textarea");
  check("the screen has focus when it opens", document.activeElement === focus);
  press("p", "KeyP", 80);
  press("ArrowLeft", "ArrowLeft", 37);
  press("q", "KeyQ", 81);
  await settle(2);
  const sentKeys = keysSent();
  check("a letter is sent", sentKeys[0] === "p", JSON.stringify(sentKeys));
  check("an arrow key is sent as the program asked for it", sentKeys[1] === `${ESC}[D` || sentKeys[1] === `${ESC}OD`, JSON.stringify(sentKeys[1]));
  check("the next letter is sent too", sentKeys[2] === "q");
  check("every key names the screen", sent.filter((s) => s.action === "screen_key").every((s) => s.kwargs.id === ID));
  check("a key goes in the screen namespace's shape", sent.filter((s) => s.action === "screen_key").every((s) => typeof s.kwargs.data === "string"));

  // The program asks for cursor keys in application mode: the arrows change with it.
  screens.frame(ID, `${ESC}[?1h`);
  await settle(2);
  press("ArrowRight", "ArrowRight", 39);
  await settle(2);
  check("an arrow follows the mode the program set", keysSent().at(-1) === `${ESC}OC`, JSON.stringify(keysSent().at(-1)));

  // Shift+Esc leaves the screen and does not reach the game.
  const before = keysSent().length;
  press("Escape", "Escape", 27, { shiftKey: true });
  await settle(2);
  check("Shift+Esc moves focus off the screen", document.activeElement !== focus && document.activeElement?.classList.contains("sh-cmd") === true, String(document.activeElement?.className));
  check("Shift+Esc is not sent to the game", keysSent().length === before);
  check("the state says where the keys went", $(".state")?.textContent?.trim() === "Click to play", $(".state")?.textContent ?? "");
  check("the border dims when the keys go elsewhere", getComputedStyle($(".tile")!).borderTopColor === token("--border-bright"), getComputedStyle($(".tile")!).borderTopColor);

  // A reconnect asks for the picture again.
  const syncs = sent.filter((s) => s.action === "screen_sync").length;
  screens.resync();
  check("a reconnect asks for the screen again", sent.filter((s) => s.action === "screen_sync").length === syncs + 1);

  // The run ends.
  screens.end({ id: ID, headline: "Game over", why: "over" });
  await settle(4);
  check("the state says it is over", $(".state")?.textContent?.trim() === "Game over", $(".state")?.textContent ?? "");
  check("the screen goes dark", Number.parseFloat(getComputedStyle($(".stage")!).opacity) < 0.6, getComputedStyle($(".stage")!).opacity);
  check("the screen keeps its last picture", text().includes("Score:"), text().slice(0, 80));
  check("the panel gives no result of its own", $$(".result, .verdict, .card, .score, footer").length === 0);
  check("closing becomes the main command", $(".head .sh-cmd")?.classList.contains("primary") === true);
  check("the border no longer lights", getComputedStyle($(".tile")!).borderTopColor === token("--border-bright"), getComputedStyle($(".tile")!).borderTopColor);
  check("it glows nowhere either", $$("*").every((el) => getComputedStyle(el).boxShadow === "none" && getComputedStyle(el).textShadow === "none"));
  const after = keysSent().length;
  press("q", "KeyQ", 81);
  await settle(2);
  check("no key is sent once it is over", keysSent().length === after);
  const resyncs = sent.filter((s) => s.action === "screen_sync").length;
  screens.resync();
  check("a finished screen is not asked for again", sent.filter((s) => s.action === "screen_sync").length === resyncs);

  // Close.
  $<HTMLButtonElement>(".head .sh-cmd")?.click();
  await settle(2);
  check("closing asks the shell to close the panel", closed.includes(ID), JSON.stringify(closed));
  check("a closed screen is forgotten", !(ID in screens.screens) && !(ID in screens.ended));

  check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));

  // Leave something to look at: a fresh screen mid game, and a verdict beside it.
  await showcase();

  const pre = document.getElementById("results")!;
  pre.textContent = results.join("\n");
  window.__screenTest = { done: true, failures, results };
}

// A second panel for a screenshot: the cabinet in play, tall enough to see the whole screen.
async function showcase(): Promise<void> {
  const box = document.createElement("div");
  box.style.cssText = "width:760px;height:580px;display:inline-flex;flex-direction:column;vertical-align:top;margin-left:12px";
  document.body.insertBefore(box, document.getElementById("results"));
  const id = "arcade-show";
  screens.open({
    id,
    type: "screen",
    title: "Invaders",
    size: [80, 24],
    controls: [["Left, Right", "move the gun"], ["Space", "fire"], ["P", "pause"], ["Q", "quit"]],
    dismissible: true,
  });
  mount(ScreenPanel, { target: box, props: { screenId: id } });
  await settle(6);
  await wait(60);
  screens.frame(id, program(2450));
  await settle(4);
}

void run();
