// Browser checks for a channel's message list following new traffic. Open
// /tests/channel.html on the Vite dev server, or run `npm run test:browser`.
//
// The real ChannelView is mounted against the real chat store with no socket;
// traffic goes through the same `handleOob` the live connection feeds. Each
// message is delivered in its own task, as socket frames are.

import { mount, tick } from "svelte";

import ChannelView from "../src/components/ChannelView.svelte";
import { chat } from "../src/lib/chat.svelte";

declare global {
  interface Window {
    __channelTest?: { done: boolean; failures: number; results: string[] };
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
const nextTask = () => new Promise((resolve) => setTimeout(resolve, 0));

let seq = 0;
function say(channel: string, text: string): void {
  chat.handleOob("channel_msg", [], { channel, text, sender: `Speaker${seq % 3}`, ts: Date.now() / 1000, msg_id: `m${seq}` });
}
async function traffic(channel: string, n: number, long = false): Promise<void> {
  for (let i = 0; i < n; i++) {
    seq++;
    say(channel, long ? `${"chatter ".repeat(30)}${seq}` : `line ${seq}`);
    await nextTask();
  }
  await settle();
}

const host = document.getElementById("host")!;
// Reactive props, so a test can hand the mounted view another channel the way
// the Channels panel does.
const props = $state({ channelKey: "ooc" });
const list = () => document.querySelector<HTMLElement>(".msgs")!;
const gap = () => {
  const el = list();
  return el.scrollHeight - el.scrollTop - el.clientHeight;
};
/** The first message whose bottom edge is below the list's top edge. */
function topMsg(): { uid: string; offset: number } | null {
  const top = list().getBoundingClientRect().top;
  for (const node of document.querySelectorAll<HTMLElement>(".msg")) {
    const r = node.getBoundingClientRect();
    if (r.bottom > top + 0.5) return { uid: node.dataset.uid ?? "", offset: r.top - top };
  }
  return null;
}
const lastText = () => Array.from(document.querySelectorAll(".msg .text")).at(-1)?.textContent ?? "";

async function run(): Promise<void> {
  chat.handleOob("channels_list", [{ key: "ooc", name: "OOC" }, { key: "lfrp", name: "LFRP" }], {});
  chat.setActive("ooc");
  mount(ChannelView, { target: host, props });
  await settle();

  await traffic("ooc", 40);
  check("socket-frame traffic keeps the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);

  await traffic("ooc", 12, true);
  check("wrapped messages keep the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);

  // The store keeps the newest 500 per channel. Past the cap an arrival drops
  // the oldest, so the list length stops changing; it must still follow.
  await traffic("ooc", 470);
  check("the channel reached its cap", (chat.messages.ooc ?? []).length === 500, `${chat.messages.ooc?.length}`);
  await traffic("ooc", 6, true);
  check("traffic past the cap keeps the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);
  check("traffic past the cap shows the newest message", lastText().endsWith(String(seq)), lastText().slice(-20));

  // A hidden panel (dockview hides an inactive tab rather than destroying it)
  // comes back at the bottom, including traffic that arrived while hidden.
  host.style.display = "none";
  await settle();
  await traffic("ooc", 5);
  host.style.display = "";
  await settle(6);
  check("a panel shown again is at the newest message", gap() < 2, `gap ${gap().toFixed(1)}`);

  // A reader who scrolled up stays where they are.
  const el = list();
  el.dispatchEvent(new WheelEvent("wheel", { deltaY: -120, bubbles: true }));
  el.scrollTop -= 400;
  el.dispatchEvent(new Event("scroll"));
  await settle();
  const before = topMsg();
  await traffic("ooc", 5);
  const after = topMsg();
  check(
    "traffic past the cap leaves a scrolled-up reader on the same message",
    !!before && before.uid === after?.uid && Math.abs(before.offset - after.offset) < 2,
    `${JSON.stringify(before)} -> ${JSON.stringify(after)}`,
  );
  const jump = document.querySelector<HTMLButtonElement>(".cv .latest");
  check("a reader who scrolled up is told about new messages", !!jump && /5 new/.test(jump.textContent ?? ""), jump?.textContent ?? "no bar");
  jump?.click();
  await settle();
  check("the new-messages bar returns to the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);
  await traffic("ooc", 3);
  check("following resumes after jumping back", gap() < 2, `gap ${gap().toFixed(1)}`);

  // Someone starts typing: the "transmitting" line takes a row from the list,
  // and the newest message must not end up underneath it.
  chat.handleOob("channel_typing", [], { channel_key: "ooc", sender_name: "Typist" });
  await settle();
  check("the typing line does not cover the newest message", gap() < 2, `gap ${gap().toFixed(1)}`);

  // Content that grows after it rendered (a web font, a zoom, a font setting).
  host.style.fontSize = "18px";
  await settle(6);
  check("content that grows after rendering keeps the bottom", gap() < 2, `gap ${gap().toFixed(1)}`);
  host.style.fontSize = "";
  await settle();

  // The Channels panel reuses one view for every channel in its rail. Leaving
  // a channel scrolled up must not leave the next one scrolled up too.
  await traffic("lfrp", 60);
  el.dispatchEvent(new WheelEvent("wheel", { deltaY: -120, bubbles: true }));
  el.scrollTop -= 500;
  el.dispatchEvent(new Event("scroll"));
  await settle();
  props.channelKey = "lfrp";
  await settle();
  check("switching channels opens the next one at its newest message", gap() < 2, `gap ${gap().toFixed(1)}`);
  await traffic("lfrp", 4);
  check("the switched-to channel follows its traffic", gap() < 2, `gap ${gap().toFixed(1)}`);

  check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));

  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__channelTest = { done: true, failures, results };
}

run().catch((error) => {
  failures++;
  results.push(`FAIL harness crashed: ${error}`);
  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__channelTest = { done: true, failures, results };
});
