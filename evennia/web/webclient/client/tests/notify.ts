// Browser checks for the tab alert: the real document title and tab icon while
// the player is away from the tab. Run with `npm run test:browser`.

import { notify } from "../src/lib/notify.svelte";
import { settings } from "../src/lib/settings.svelte";

declare global {
  interface Window {
    __notifyTest?: { done: boolean; failures: number; results: string[] };
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
const icon = () => document.querySelector<HTMLLinkElement>('link[rel~="icon"]')?.href ?? "";
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function run(): Promise<void> {
  settings.reduceMotion = false;
  settings.screenreader = false;
  settings.tabAlert = "any";
  const original = icon();
  notify.init();
  await wait(100); // the badged icon is drawn once, asynchronously

  notify.activity();
  check("output while looking at the tab changes nothing", document.title === "Underspire", document.title);

  window.dispatchEvent(new Event("blur"));
  notify.activity();
  check("output while away flashes the title", document.title === "▶ New activity · Underspire", document.title);
  check("output while away badges the tab icon", icon().startsWith("data:image/png"), icon().slice(0, 30));
  await wait(1100);
  check("the title blinks", document.title === "● Underspire", document.title);

  notify.ping("Tell", "hello", false);
  check("a message to the player is named and counted", document.title === "▶ New message · Underspire", document.title);
  await wait(1100);
  check("the count shows between blinks", /^\(1\) Underspire$/.test(document.title) || document.title.startsWith("▶"), document.title);

  window.dispatchEvent(new Event("focus"));
  check("coming back restores the title", document.title === "Underspire", document.title);
  check("coming back restores the tab icon", icon() === original, icon().slice(0, 40));
  await wait(1100);
  check("coming back stops the blink", document.title === "Underspire", document.title);

  settings.tabAlert = "direct";
  window.dispatchEvent(new Event("blur"));
  notify.activity();
  check("direct-only mode ignores ordinary output", document.title === "Underspire", document.title);
  window.dispatchEvent(new Event("focus"));

  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__notifyTest = { done: true, failures, results };
}

run().catch((error) => {
  failures++;
  results.push(`FAIL harness crashed: ${error}`);
  window.__notifyTest = { done: true, failures, results };
});
