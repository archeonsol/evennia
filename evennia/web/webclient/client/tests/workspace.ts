// Browser checks for which panels the docked workspace keeps. Run with
// `npm run test:browser`.
//
// A layout is saved per browser, not per account, so one saved from a staff
// session comes back for whoever logs in next on that browser.

import { mount, tick, unmount } from "svelte";

import Workspace from "../src/components/Workspace.svelte";
import SimpleWorkspace from "../src/components/SimpleWorkspace.svelte";
import { activity } from "../src/lib/activity.svelte";
import { simple } from "../src/lib/simpleLayout.svelte";
import { puppets } from "../src/lib/puppets.svelte";
import { tickets } from "../src/lib/tickets.svelte";
import { dock } from "../src/lib/dock.svelte";

declare global {
  interface Window {
    __workspaceTest?: { done: boolean; failures: number; results: string[] };
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
async function settle(frames = 4): Promise<void> {
  for (let i = 0; i < frames; i++) {
    await tick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  }
  await tick();
}
const has = (id: string) => !!dock.api?.getPanel(id);
const title = (id: string) => (dock.api?.getPanel(id) as any)?.title ?? "";

async function run(): Promise<void> {
  localStorage.removeItem("underspire.layout.v2");
  const host = document.getElementById("host")!;

  // A staff session: the queue panel appears and is saved into the layout.
  let app = mount(Workspace, { target: host });
  await settle();
  tickets.handleOob("ticket_role", [], { staff: true });
  await settle();
  check("staff get the ticket queue", has("tickets"));
  check("the staff panel is named apart from My requests", title("tickets") === "Tickets", title("tickets"));
  dock.api?.getPanel("log")?.api.setActive(); // any layout change saves it
  await settle();
  check("the layout was saved with the queue in it", (localStorage.getItem("underspire.layout.v2") ?? "").includes('"tickets"'));
  unmount(app);

  // A player on the same browser: the saved layout brings the panel back ...
  tickets.staff = false;
  tickets.staffKnown = false;
  app = mount(Workspace, { target: host });
  await settle();
  check("a restored layout keeps the queue until the role is known", has("tickets"));

  // ... until the server says this session is not staff.
  tickets.handleOob("ticket_role", [], { staff: false });
  await settle();
  check("a player loses a queue panel restored from a staff layout", !has("tickets"));
  unmount(app);

  const calls: string[] = [];
  activity.connect(async (_ns, action) => {
    calls.push(action);
    if (action === "activity_subscribe") return { stream_id: "workspace", last_seq: 0, events: [], watches: { characters: [], locations: [] }, role: { allowed: true, can_puppet: false } };
    if (action === "puppet_add") return { puppets: [{ npc_id: 21, slot: 2, name: "Toma" }] };
    return { ok: true };
  }, (entries) => puppets.setManifest(entries));
  app = mount(Workspace, { target: host });
  await settle();
  activity.setRole({ allowed: true, can_puppet: false });
  await settle();
  check("observe authority adds Activity independently of ticket role", has("activity") && !has("tickets"));
  // The game sends logged_in and then the role on every login: main.ts clears on logged_in.
  const beforeLogin = dock.api?.getPanel("activity");
  activity.clear();
  await settle();
  activity.setRole({ allowed: true, can_puppet: false });
  await settle();
  check("a login refresh keeps the same Activity panel", !!beforeLogin && dock.api?.getPanel("activity") === beforeLogin);
  dock.api?.getPanel("activity")?.api.close();
  await settle();
  check("closed Activity stays closed in docked layout", !has("activity"));
  dock.openView("activity");
  await settle();
  check("Activity can be reopened", has("activity"));
  dock.api?.getPanel("activity")?.api.setActive();
  await activity.mutate("puppet_add", { npc_id: 21 });
  await settle();
  check("Add puppet leaves Activity selected and no NPC terminal open", dock.api?.activePanel?.id === "activity" && puppets.activeId === null);
  activity.setRole({ allowed: false, can_puppet: false });
  await settle();
  check("revocation removes Activity from docked layout", !has("activity") && activity.events.length === 0);
  unmount(app);

  app = mount(SimpleWorkspace, { target: host });
  await settle();
  activity.setRole({ allowed: true, can_puppet: false });
  await settle();
  check("observe authority adds Activity in simple layout", simple.has("activity"));
  calls.length = 0;
  simple.close("activity");
  await settle();
  check("closed Activity stays closed in simple layout", !simple.has("activity"));
  check("closing Activity unsubscribes", calls.includes("activity_unsubscribe"));
  activity.setRole({ allowed: false, can_puppet: false });
  await settle();
  unmount(app);

  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__workspaceTest = { done: true, failures, results };
}

run().catch((error) => {
  failures++;
  results.push(`FAIL harness crashed: ${error}`);
  window.__workspaceTest = { done: true, failures, results };
});
