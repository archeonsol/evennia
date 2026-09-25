// Browser checks for the ticket panels. Run with `npm run test:browser`.
//
// The real panels and store run against a stubbed connection: every RPC the
// panels make is recorded and answered from fixtures, and any command they
// type is recorded too, because a panel action that types a command is the
// bug (its confirmation lands in the terminal).

import { mount, tick } from "svelte";

import MyTicketsPanel from "../src/components/MyTicketsPanel.svelte";
import TicketsPanel from "../src/components/TicketsPanel.svelte";
import Toasts from "../src/components/Toasts.svelte";
import { chat } from "../src/lib/chat.svelte";
import { connection } from "../src/lib/evennia.svelte";
import { toasts } from "../src/lib/toasts.svelte";

declare global {
  interface Window {
    __ticketsTest?: { done: boolean; failures: number; results: string[] };
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
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const now = Math.floor(Date.now() / 1000);
const mineRows = [
  { id: "aaaa1111bbbb", short_id: "aaaa1111", kind: "request", label: "Request", status: "waiting", subject: "Stuck door", preview: "try now", updated: now - 60, approvable: false },
  { id: "cccc2222dddd", short_id: "cccc2222", kind: "request", label: "Request", status: "pending", subject: "Sheet fix", preview: "please", updated: now - 3600, approvable: false },
];
const doorThread = {
  ...mineRows[0],
  messages: [
    { origin: "player", sender: "Vesna", text: "The door will not open.", ts: now - 600 },
    { origin: "system", sender: "System", text: "Mira is handling this.", ts: now - 300 },
    { origin: "staff", sender: "Mira", text: "Try it now.", ts: now - 60 },
  ],
};
const calls: { action: string; data: any }[] = [];
const commands: string[] = [];
(connection as any).request = async (_ns: string, action: string, data: any) => {
  calls.push({ action, data });
  if (action === "my_tickets") {
    const q = String(data?.search ?? "").toLowerCase();
    return { tickets: q ? mineRows.filter((r) => r.subject.toLowerCase().includes(q)) : mineRows };
  }
  if (action === "my_ticket") return doorThread;
  if (action === "my_ticket_act") {
    if (data.action === "reply") {
      return { message: "Sent.", ticket: { ...doorThread, status: "pending", messages: [...doorThread.messages, { origin: "player", sender: "Vesna", text: data.text, ts: now }] } };
    }
    return { message: "Withdrawn.", ticket: { ...doorThread, status: "withdrawn" } };
  }
  if (action === "ticket_act") {
    return { message: data.action === "close" ? "Closed. The player was told." : "Done.", ticket: { ...doorThread, status: data.action === "close" ? "closed" : "pending", assignee: "Mira" } };
  }
  if (action === "ticket_list") return { tickets: [] };
  throw new Error(`unexpected ${action}`);
};
connection.sendCommand = (line: string) => {
  commands.push(line);
  if (line.startsWith("@ticket ")) chat.handleOob("ticket_thread", [], { ...doorThread });
};
(connection as any).state = "open";

async function run(): Promise<void> {
  mount(Toasts, { target: document.getElementById("toasts")! });

  // ---- player: My Tickets -------------------------------------------------
  chat.staff = false;
  const mine = document.getElementById("mine")!;
  mount(MyTicketsPanel, { target: mine });
  await settle();
  await wait(50);
  await settle();
  const rows = () => Array.from(mine.querySelectorAll<HTMLElement>(".row"));
  check("My Tickets lists open tickets", rows().length === 2, `${rows().length}`);

  const waiting = mine.querySelector<HTMLButtonElement>('.chips button[aria-checked="false"]');
  mine.querySelectorAll<HTMLButtonElement>(".chips button")[1].click(); // Waiting on you
  await settle();
  check("Waiting on you shows only what waits on the player", rows().length === 1 && rows()[0].textContent!.includes("Stuck door"), rows().map((r) => r.textContent).join("|"));
  mine.querySelectorAll<HTMLButtonElement>(".chips button")[0].click();
  await settle();

  const search = mine.querySelector<HTMLInputElement>(".search")!;
  search.value = "sheet";
  search.dispatchEvent(new Event("input", { bubbles: true }));
  await wait(400);
  await settle();
  const searched = calls.filter((c) => c.action === "my_tickets").at(-1);
  check("search asks the server", searched?.data?.search === "sheet", JSON.stringify(searched?.data));
  check("search narrows the list", rows().length === 1 && rows()[0].textContent!.includes("Sheet fix"), rows().map((r) => r.textContent).join("|"));
  search.value = "";
  search.dispatchEvent(new Event("input", { bubbles: true }));
  await wait(100);
  await settle();

  rows()[0].click();
  await settle();
  check("opening a ticket shows the system notice as a notice", !!mine.querySelector(".sys") && mine.querySelector(".sys")!.textContent!.includes("handling"));
  check("staff lines are marked staff", mine.querySelector(".m.staffmsg .role")?.textContent?.trim() === "staff");

  const box = mine.querySelector<HTMLTextAreaElement>(".reply textarea")!;
  box.value = "Still stuck.";
  box.dispatchEvent(new Event("input", { bubbles: true }));
  await settle();
  box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await wait(50);
  await settle();
  const replied = calls.filter((c) => c.action === "my_ticket_act").at(-1);
  check("a reply goes through the RPC", replied?.data?.action === "reply" && replied?.data?.text === "Still stuck.", JSON.stringify(replied?.data));
  check("the panel says it was sent", mine.querySelector(".fb")?.textContent?.includes("Sent.") === true, mine.querySelector(".fb")?.textContent ?? "none");
  check("the reply box is cleared", box.value === "", box.value);

  // A staff reply to a ticket the player is not looking at raises a toast.
  mine.querySelector<HTMLButtonElement>(".back")!.click();
  await settle();
  chat.myTicket = null;
  chat.handleOob("ticket_msg", [], {
    id: "cccc2222dddd", short_id: "cccc2222", label: "Request", subject: "Sheet fix", text: "Fixed your sheet.",
    sender: "Mira", origin: "staff", audience: "owner", status: "waiting", ts: now,
  });
  await settle();
  const toast = document.querySelector<HTMLButtonElement>(".toast");
  check("a staff reply with the ticket closed raises a toast", !!toast && toast.textContent!.includes("Staff replied"), toast?.textContent ?? "none");
  toast?.click();
  await wait(50);
  await settle();
  check("the toast opens that ticket", !!chat.myTicket, JSON.stringify(chat.myTicket?.id ?? null));
  toasts.list.forEach((t) => toasts.dismiss(t.id));

  // ---- staff: Ticket Queue -------------------------------------------------
  chat.staff = true;
  chat.handleOob("ticket_inbox", [], { tickets: mineRows });
  toasts.list.forEach((t) => toasts.dismiss(t.id));
  const queue = document.getElementById("queue")!;
  mount(TicketsPanel, { target: queue });
  await settle();
  const qsearch = queue.querySelector<HTMLInputElement>(".search")!;
  qsearch.value = "door";
  qsearch.dispatchEvent(new Event("input", { bubbles: true }));
  await settle();
  check("the queue narrows as staff type", queue.querySelectorAll(".row").length === 1, `${queue.querySelectorAll(".row").length}`);
  queue.querySelector<HTMLButtonElement>(".row")!.click();
  await settle();
  const before = commands.length;
  const close = Array.from(queue.querySelectorAll<HTMLButtonElement>(".act")).find((b) => b.textContent === "Close");
  close?.click();
  await wait(50);
  await settle();
  const acted = calls.filter((c) => c.action === "ticket_act").at(-1);
  check("Close goes through the RPC", acted?.data?.action === "close", JSON.stringify(acted?.data));
  check("Close types no command (nothing lands in the terminal)", commands.length === before, commands.slice(before).join("|"));
  check("the queue says what happened", queue.querySelector(".fb")?.textContent?.includes("Closed") === true, queue.querySelector(".fb")?.textContent ?? "none");
  check("a closed ticket offers Reopen", Array.from(queue.querySelectorAll(".act")).some((b) => b.textContent === "Reopen"));

  check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));
  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__ticketsTest = { done: true, failures, results };
}

run().catch((error) => {
  failures++;
  results.push(`FAIL harness crashed: ${error}`);
  window.__ticketsTest = { done: true, failures, results };
});
