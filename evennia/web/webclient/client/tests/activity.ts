import { mount, tick } from "svelte";
import ActivityPanel from "../src/components/ActivityPanel.svelte";
import { activity, type ActivityEvent } from "../src/lib/activity.svelte";
import { puppets } from "../src/lib/puppets.svelte";
import { settings } from "../src/lib/settings.svelte";
import "../src/styles/themes.css";
import "../src/styles/shell.css";

declare global {
  interface Window { __activityTest?: { done: boolean; failures: number; results: string[] } }
}
const results: string[] = [];
let failures = 0;
function check(name: string, condition: boolean) {
  results.push(`${condition ? "PASS" : "FAIL"} ${name}`);
  if (!condition) failures++;
}
async function settle() {
  for (let i = 0; i < 5; i++) {
    await tick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  }
}
const calls: { action: string; data: any }[] = [];
function event(seq: number): ActivityEvent {
  const npc = { kind: "npc" as const, id: 20, name: "Toma" };
  const kind = seq % 3 === 0 ? "npc.action" : seq % 3 === 1 ? "handset.direct" : "looc";
  return {
    id: `browser:${seq}`, seq, ts_ms: Date.UTC(2026, 8, 30, 14, seq % 60), kind,
    actor: { kind: "character", id: 10, name: "Vesna" },
    targets: kind === "npc.action" ? [npc] : [], npc_targets: kind === "npc.action" ? [npc] : [],
    target_count: kind === "npc.action" ? 1 : 0,
    location: { kind: "location", id: 30, name: "Copper Lantern" },
    body: kind === "npc.action" ? "Vesna asks Toma whether anyone has seen the courier tonight." : kind === "looc" ? "Can someone help with the scene at the counter?" : "Meet me by the lift. The courier is late again.", meta: {},
  };
}
let watches = { characters: [] as { id: number; label: string }[], locations: [] as { id: number; label: string }[] };
let denyPuppet = false;
activity.connect(async (_ns, action, data: any) => {
  calls.push({ action, data });
  if (action === "activity_subscribe") return { stream_id: "browser", last_seq: 50, events: Array.from({ length: 50 }, (_, i) => event(i + 1)), watches, role: { allowed: true, can_puppet: true } };
  if (action === "activity_search") return { results: [{ kind: "location", id: 99, name: "North Gallery" }] };
  if (action === "activity_watch") { watches = { ...watches, locations: [{ id: data.id, label: "North Gallery" }] }; return { watches }; }
  if (action === "activity_unwatch") { watches = { ...watches, locations: [] }; return { watches }; }
  if (action === "puppet_add") {
    if (denyPuppet) throw new Error("Puppet access denied.");
    return { puppets: [{ npc_id: data.npc_id, slot: 2, name: "Toma" }] };
  }
  if (action === "activity_unsubscribe") return { ok: true };
  throw new Error(`Unexpected action ${action}`);
}, (entries) => puppets.setManifest(entries));
activity.setRole({ allowed: true, can_puppet: true });
settings.screenreader = false;
const host = document.getElementById("host")!;
mount(ActivityPanel, { target: host });
const button = (label: string) => [...host.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.trim() === label)!;

async function run() {
  await settle();
  for (let first = 51; first <= 999; first += 16) {
    const events = Array.from({ length: Math.min(16, 1000 - first) }, (_, i) => event(first + i));
    activity.batch({ stream_id: "browser", first_seq: first, last_seq: events.at(-1)!.seq, events });
  }
  await settle();
  check("virtual list has bounded DOM", host.querySelectorAll(".event").length < 60 && activity.events.length === 999);
  check("no horizontal overflow", host.scrollWidth <= host.clientWidth);
  const body = host.querySelector(".body")!;
  check("feed body is at least 14px", parseFloat(getComputedStyle(body).fontSize) >= 14);
  check("controls are at least 12px", [...host.querySelectorAll(".segment button")].every((node) => parseFloat(getComputedStyle(node).fontSize) >= 12));
  const feed = host.querySelector<HTMLElement>(".feed")!;
  check("follows latest after burst", feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40);

  button("NPC").click();
  await settle();
  check("category filter excludes other event kinds", activity.filtered("npc", false, "").length === 333);
  [...host.querySelectorAll<HTMLButtonElement>(".event-trigger")].at(-1)!.click();
  await settle();
  denyPuppet = true;
  button("Add puppet").click();
  await settle();
  check("puppet denial is visible", !!host.querySelector('[role="alert"]')?.textContent?.includes("denied"));
  denyPuppet = false;
  button("Add puppet").click();
  await settle();
  check("add puppet updates list without taking control", puppets.feeds.has("20") && puppets.activeId === null && button("In puppets").disabled);

  button("All").click();
  button("Watch (0)").click();
  await settle();
  const input = host.querySelector<HTMLInputElement>('[aria-label="Find a character, NPC or location to watch"]')!;
  input.value = "North";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 300));
  await settle();
  host.querySelector<HTMLButtonElement>(".watch-results button")!.click();
  await settle();
  check("arbitrary location absent from feed can be watched", activity.watches.locations[0]?.id === 99 && !activity.events.some((item) => item.location?.id === 99));
  button("Watched").click();
  await settle();
  check("watched empty state explains no matches", !!host.querySelector(".empty")?.textContent?.includes("No activity matches"));
  button("Global").click();
  button("Watch (1)").click();
  await settle();

  button("Pause").click();
  activity.batch({ stream_id: "browser", first_seq: 1000, last_seq: 1000, events: [event(1000)] });
  await settle();
  check("pause freezes visible cutoff and counts arrivals", activity.filtered("all", false, "").at(-1)?.seq === 999 && !!button("Latest (1)"));
  button("Resume").click();
  await settle();
  feed.scrollTop = 0;
  feed.dispatchEvent(new Event("scroll"));
  await settle();
  activity.batch({ stream_id: "browser", first_seq: 1001, last_seq: 1001, events: [event(1001)] });
  await settle();
  check("scrolling up keeps reading position", feed.scrollTop < 100 && host.querySelector(".footer")!.textContent!.includes("Reading"));
  button("Latest (1)").click();
  await settle();
  check("latest restores follow", host.querySelector(".footer")!.textContent!.includes("Following"));
  settings.screenreader = true;
  await settle();
  check("screen reader has bounded nonvirtual rows", host.querySelectorAll(".event.reader").length === 1000);
  settings.screenreader = false;
  await settle();

  document.getElementById("results")!.textContent = results.join("\n");
  window.__activityTest = { done: true, failures, results };
}
run().catch((err) => {
  check(`harness crashed: ${err}`, false);
  window.__activityTest = { done: true, failures, results };
});
