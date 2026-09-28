// Browser checks for which panels the docked workspace keeps. Run with
// `npm run test:browser`.
//
// A layout is saved per browser, not per account, so one saved from a staff
// session comes back for whoever logs in next on that browser.

import { mount, tick, unmount } from "svelte";

import Workspace from "../src/components/Workspace.svelte";
import { chat } from "../src/lib/chat.svelte";
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
  chat.handleOob("ticket_role", [], { staff: true });
  await settle();
  check("staff get the ticket queue", has("tickets"));
  check("the staff panel is named apart from My Tickets", title("tickets") === "Ticket Queue", title("tickets"));
  dock.api?.getPanel("log")?.api.setActive(); // any layout change saves it
  await settle();
  check("the layout was saved with the queue in it", (localStorage.getItem("underspire.layout.v2") ?? "").includes('"tickets"'));
  unmount(app);

  // A player on the same browser: the saved layout brings the panel back ...
  chat.staff = false;
  chat.staffKnown = false;
  app = mount(Workspace, { target: host });
  await settle();
  check("a restored layout keeps the queue until the role is known", has("tickets"));

  // ... until the server says this session is not staff.
  chat.handleOob("ticket_role", [], { staff: false });
  await settle();
  check("a player loses a queue panel restored from a staff layout", !has("tickets"));
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
