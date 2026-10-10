// Browser checks for the ticket panels. Run with `npm run test:browser`.
//
// The real panels and stores run against a stubbed connection: every request the
// panels make is recorded and answered from fixtures, and any command they type
// is recorded too, because a panel action that types a command is the bug (its
// confirmation lands in the terminal). The checks are about what a person sees:
// the order, the words, the sizes, and what each button asks the server.

import { mount, tick, unmount } from "svelte";

import MyTicketsPanel from "../src/components/MyTicketsPanel.svelte";
import TicketsPanel from "../src/components/TicketsPanel.svelte";
import Toasts from "../src/components/Toasts.svelte";
import { connection } from "../src/lib/evennia.svelte";
import { tickets } from "../src/lib/tickets.svelte";
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

function type(el: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

/** Text a reader sees that is set smaller than 12px. */
function smallText(root: HTMLElement): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  while ((node = walker.nextNode())) {
    const text = node.textContent?.trim();
    const el = node.parentElement;
    if (!text || !el) continue;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") continue;
    if (el.offsetParent === null && style.position !== "fixed") continue;
    const size = parseFloat(style.fontSize);
    if (size < 11.99) out.push(`${size}px: ${text.slice(0, 30)}`);
  }
  return out;
}

const shown = (el: Element | null) => !!el && getComputedStyle(el).display !== "none" && (el as HTMLElement).offsetWidth > 0;

const now = Math.floor(Date.now() / 1000);

function row(over: Record<string, unknown>): any {
  return {
    id: "x",
    number: 1,
    ref: "#1",
    kind: "request",
    label: "Player Request",
    approvable: false,
    status: "pending",
    state: "unanswered",
    presence: "online",
    seen: 0,
    account_id: 10,
    account_name: "kade",
    account_online: true,
    requester_name: "Kade",
    assignee: "",
    assignee_id: null,
    priority: 0,
    created: now - 600,
    updated: now - 600,
    unanswered_since: now - 600,
    answered_at: 0,
    waited_minutes: 10,
    clock: "ok",
    preview: "",
    title: "Ticket",
    subject: "",
    sort: [0, 2, 0, -1, -1],
    ...over,
  };
}

const dov = row({
  id: "dov", number: 1042, ref: "#1042", kind: "puppet", label: "Puppet Request", presence: "room",
  requester_name: "Mira Thane", account_name: "mthane", priority: 1, waited_minutes: 7, unanswered_since: now - 420,
  title: "Dov Kessler, the scarred broker", preview: "Close the deal for the crate before the lift leaves.",
  sort: [0, 0, 0, now - 420, 1042],
});
const door = row({
  id: "door", number: 1043, ref: "#1043", title: "Cannot open my apartment door", subject: "Cannot open my apartment door",
  requester_name: "Dena", account_name: "dena", preview: "I tried unlock, key, open door.", waited_minutes: 25, clock: "overdue",
  unanswered_since: now - 1500, sort: [0, 2, 0, -(now - 1500), -1043],
});
const crash = row({
  id: "crash", number: 1039, ref: "#1039", kind: "bug", label: "Bug Report", title: "Crash when I look at the market board",
  requester_name: "Tarn", account_name: "tarn", presence: "offline", account_online: false, priority: 2, waited_minutes: 300,
  clock: "long", unanswered_since: now - 18000, preview: "The game says something went wrong.",
  sort: [0, 2, 0, -(now - 18000), -1039],
});
const clerk = row({
  id: "clerk", number: 1038, ref: "#1038", kind: "puppet", label: "Puppet Request", presence: "offline", account_online: false,
  requester_name: "Kade Voss", title: "The tall clerk", preview: "Asked about the missing crate.", waited_minutes: 180,
  unanswered_since: now - 10800, sort: [0, 4, 0, now - 10800, 1038],
});
const answered = row({
  id: "ans", number: 1030, ref: "#1030", status: "waiting", state: "answered", title: "Sheet fix", requester_name: "Wick",
  assignee: "Aliki", assignee_id: 9, unanswered_since: 0, updated: now - 3600, preview: "Fixed.", sort: [1, 0, -(now - 3600), 0, -1030],
});

const dovThread = {
  ...dov,
  facts: [
    { label: "NPC", value: "Dov Kessler, #1412" },
    { label: "The player calls it", value: "the scarred broker" },
    { label: "Wants", value: "Close the deal for the crate before the lift leaves." },
    { label: "Player is in", value: "Lift Lobby" },
  ],
  messages: [
    { origin: "player", sender: "Mira Thane", text: "Please can Dov accept forty credits?", ts: now - 420, visibility: "player" },
    { origin: "system", sender: "System", text: "Aliki is handling this.", ts: now - 300, visibility: "player" },
    { origin: "staff", sender: "Aliki", text: "Diving now. Dov is at the lift.", ts: now - 280, visibility: "internal" },
  ],
};

const calls: { action: string; data: any }[] = [];
const commands: string[] = [];
let puppetOk = true;
(connection as any).request = async (_ns: string, action: string, data: any) => {
  calls.push({ action, data });
  switch (action) {
    case "ticket_get":
      return data.id === "dov" ? dovThread : { ...door, facts: [], messages: [] };
    case "ticket_act":
      if (data.action === "puppet") {
        return {
          message: "Claimed. Moved you to Lift Lobby.",
          ticket: { ...dovThread, assignee: "Mira", assignee_id: 7 },
          puppet: { ok: puppetOk, message: puppetOk ? "Moved you to Lift Lobby." : "Go in-game first, then puppet it.", command: "@puppet #1412" },
        };
      }
      if (data.action === "duty") return { message: data.on ? "You are on duty." : "You are off duty.", duty: !!data.on };
      return {
        message: data.action === "close" ? "Closed. The player was told." : data.action === "reply" ? (data.internal ? "Staff note added." : "Reply sent.") : "Done.",
        ticket: { ...dovThread, assignee: "Mira", assignee_id: 7 },
      };
    case "ticket_replies":
      if (data.op === "expand") return { text: "Hi Mira Thane, this is #1042. Aliki" };
      return { replies: [{ id: 1, name: "thanks", body: "Hi {name}, this is {number}. {staff}", shared: false, mine: true }] };
    case "ticket_list":
      return { tickets: [], has_next: false };
    case "ticket_bug_detail":
      return { available: true, reporter: "tarn", character: "Tarn", location: "Market", traceback: "Traceback BOOM", character_state: {} };
    case "my_tickets": {
      const q = String(data?.search ?? "").toLowerCase();
      const rows = mineRows.filter((r) => (data?.closed ? true : r.status !== "closed"));
      return { tickets: q ? rows.filter((r) => r.title.toLowerCase().includes(q)) : rows };
    }
    case "my_ticket":
      return { ...mineRows.find((r) => r.id === data.id), messages: mineThread };
    case "my_ticket_act":
      return { message: "Sent.", ticket: { ...mineRows[0], status: "pending", messages: [...mineThread, { origin: "player", sender: "Vesna", text: data.text, ts: now }] } };
    case "ticket_form":
      // The game's answer. The question differs from the client's own default so the check below proves the panel shows the game's words.
      return {
        pick: "What do you need today?",
        kinds: [
          { kind: "request", name: "Ask staff a question", hint: "Anything you need staff to look at." },
          { kind: "bug", name: "Report a bug", hint: "Something in the game is broken." },
          { kind: "report", name: "Report another player", hint: "Report a player to senior staff for investigation." },
          { kind: "puppet", name: "Ask for an NPC to be puppeted", hint: "Send it from the room the NPC is in. You can leave and wait for a reply." },
        ],
        summary: "Brief summary",
        details: "Details",
        placeholders: { npc: "The tall clerk, as you see them", contact: "A handset ID, or another way to reach your character" },
        npc: "Which NPC",
        said: "What has happened so far",
        goal: "What you want from the scene",
        contact: "Contact information",
        category: "What kind of problem",
        categories: ["Movement", "Combat", "Roleplay", "NPCs", "Vehicles", "Other"],
        severity: "How bad",
        severities: [
          { key: "Trivial", text: "Trivial: a typo or a small glitch" },
          { key: "Minor", text: "Minor: annoying, but easy to get around" },
          { key: "Moderate", text: "Moderate: broken, with no way around it" },
          { key: "Severe", text: "Severe: a major failure, or a crash" },
          { key: "Critical", text: "Critical: the game stops, or an exploit" },
        ],
        severity_advice: "Pick the lowest one that fits. It helps staff sort bugs and does not bring a reply sooner.",
        notes: { puppet: "You can leave and wait for a reply. Leave your character's contact information so staff can reach you." },
      };
    case "ticket_suggest":
      return { topics: [{ key: "doors", summary: "How doors and locks work." }] };
    case "ticket_open":
      return { message: "Request sent. Staff have been told.", ticket: { ...mineRows[0], id: "new", ref: "#1100", title: data.subject || data.title || "New", messages: [] } };
  }
  throw new Error(`unexpected ${action}`);
};
connection.sendCommand = (line: string) => {
  commands.push(line);
};
(connection as any).state = "open";

const mineRows: any[] = [
  row({ id: "m1", number: 1043, ref: "#1043", title: "Cannot open my apartment door", subject: "Cannot open my apartment door", status: "waiting", state: "answered", unread: true, updated: now - 120, preview: "Which door, and does the rent show as paid?", label: "Player Request" }),
  row({ id: "m2", number: 1042, ref: "#1042", kind: "puppet", label: "Puppet Request", title: "the scarred broker", status: "pending", unread: false, updated: now - 420, preview: "Close the deal for the crate." }),
  row({ id: "m3", number: 1001, ref: "#1001", title: "Old question", status: "closed", state: "closed", updated: now - 86400 * 3, label: "Player Request" }),
];
const mineThread = [
  { origin: "player", sender: "Vesna", text: "The door will not open.", ts: now - 600 },
  { origin: "system", sender: "System", text: "Mira is handling this.", ts: now - 300, news: false },
  { origin: "staff", sender: "Mira", text: "Which door, and does the rent show as paid?", ts: now - 120 },
];

async function run(): Promise<void> {
  mount(Toasts, { target: document.getElementById("toasts")! });

  // ---- staff, a wide panel -------------------------------------------------
  tickets.handleOob("ticket_role", [], { staff: true, account_id: 7, duty: true });
  tickets.canHistory = true;
  // Handed over in the wrong order on purpose: the server's key is the order.
  tickets.handleOob("ticket_inbox", [], { tickets: [clerk, door, answered, crash, dov] });
  const queue = document.getElementById("queue")!;
  const wide = mount(TicketsPanel, { target: queue });
  await settle();

  const titles = () => Array.from(queue.querySelectorAll(".tk-row-title")).map((e) => e.textContent?.trim());
  check(
    "the list is in the server's order, not the order it arrived in",
    JSON.stringify(titles()) ===
      JSON.stringify(["Dov Kessler, the scarred broker", "Cannot open my apartment door", "Crash when I look at the market board", "The tall clerk"]),
    titles().join(" | "),
  );
  const viewLabels = Array.from(queue.querySelectorAll(".tk-view")).map((e) => e.textContent?.replace(/\s+/g, " ").trim());
  check(
    "the views are named in plain words with their counts",
    viewLabels.slice(0, 5).join("|") === "Mine0|Unanswered4|Answered1|Online3|All5",
    viewLabels.join("|"),
  );
  check("History is offered to staff who may close tickets", viewLabels.includes("History"));
  const first = queue.querySelector<HTMLElement>(".tk-row")!;
  check("the first row says who is in the room, in words", first.textContent!.includes("Mira Thane is in the room"), first.textContent ?? "");
  check("the first row carries its number", first.textContent!.includes("#1042"));
  check("a player in the room gets the green dot and stripe", !!first.querySelector(".tk-dot.here") && first.classList.contains("here"));
  const rows0 = Array.from(queue.querySelectorAll<HTMLElement>(".tk-row"));
  check("an offline player's dot is hollow", !!rows0[2].querySelector(".tk-dot.off"));
  check("a puppet request whose player is gone fades", rows0[3].classList.contains("faint"));
  check("an urgent, long-waiting bug is marked hot", rows0[2].classList.contains("hot"));
  check("the clock shows how long, and goes red only when well past its time", rows0[2].querySelector(".tk-clock.bad")?.textContent === "5h" && !rows0[0].querySelector(".tk-clock.bad"), rows0[2].querySelector(".tk-clock")?.textContent ?? "");
  check("no word of the old labels is on the panel", !/needs reply|on player|pending|waiting on|unclaimed/i.test(queue.textContent ?? ""), queue.textContent ?? "");
  check("no text is set below 12px", smallText(queue).length === 0, smallText(queue).slice(0, 5).join(" | "));

  // A view narrows the list.
  queue.querySelectorAll<HTMLButtonElement>(".tk-view")[3].click(); // Online
  await settle();
  check("Online shows the players who are around, the unanswered first", JSON.stringify(titles()) === JSON.stringify(["Dov Kessler, the scarred broker", "Cannot open my apartment door", "Sheet fix"]), titles().join("|"));
  queue.querySelectorAll<HTMLButtonElement>(".tk-view")[2].click(); // Answered
  await settle();
  check("Answered shows only what has been answered", JSON.stringify(titles()) === JSON.stringify(["Sheet fix"]), titles().join("|"));
  queue.querySelectorAll<HTMLButtonElement>(".tk-view")[1].click(); // Unanswered
  await settle();

  // Search narrows as staff type, by number or by words.
  const search = queue.querySelector<HTMLInputElement>(".tk-search")!;
  type(search, "#1043");
  await settle();
  check("a number finds its ticket", titles().length === 1 && titles()[0] === "Cannot open my apartment door", titles().join("|"));
  type(search, "scarred");
  await settle();
  check("words find a ticket by what it says", titles().length === 1 && titles()[0].startsWith("Dov"), titles().join("|"));
  type(search, "");
  await settle();

  // Opening a ticket on a wide panel keeps the list beside it.
  queue.querySelectorAll<HTMLElement>(".tk-row")[0].click();
  await wait(30);
  await settle();
  check("opening a ticket asks the server for it by id", calls.filter((c) => c.action === "ticket_get").at(-1)?.data?.id === "dov");
  check("the list stays beside the open ticket on a wide panel", shown(queue.querySelector(".tk-pane-list")) && shown(queue.querySelector(".tk-pane-detail")));
  check("the Back button is not shown when the list is beside it", !shown(queue.querySelector(".tk-back")));
  const card = Array.from(queue.querySelectorAll(".tk-card dt")).map((e) => e.textContent);
  check("the facts are labelled in plain words", card.includes("NPC") && card.includes("Wants") && card.includes("Player is in"), card.join("|"));
  check("the header says the player is in the room, and where", queue.querySelector(".tk-ds")!.textContent!.includes("Mira Thane is in the room, Lift Lobby"), queue.querySelector(".tk-ds")?.textContent ?? "");
  check("a staff note is marked as one and says only staff see it", !!queue.querySelector(".tk-msg.note") && queue.querySelector(".tk-msg.note")!.textContent!.includes("Only staff see this"));
  check("a system line is a line, not a message", queue.querySelector(".tk-sys")?.textContent?.includes("Aliki is handling this.") === true);
  check("no raw field names are shown", !/NPC_TYPED|NPC_ID|npc_key/i.test(queue.textContent ?? ""));

  // Puppet: one request, the server moves us, then we puppet the NPC.
  const before = commands.length;
  const puppet = Array.from(queue.querySelectorAll<HTMLButtonElement>(".tk-btn")).find((b) => b.textContent?.trim() === "Puppet");
  check("a puppet request offers Puppet as its main button", !!puppet && puppet.classList.contains("primary"));
  check("and a plain Claim beside it", Array.from(queue.querySelectorAll<HTMLButtonElement>(".tk-btn")).some((b) => b.textContent?.trim() === "Claim"));
  puppet?.click();
  await wait(30);
  await settle();
  const puppeted = calls.filter((c) => c.action === "ticket_act").at(-1);
  check("Puppet is one request", puppeted?.data?.action === "puppet" && puppeted?.data?.id === "dov", JSON.stringify(puppeted?.data));
  check("after it the NPC is puppeted with the command the server gave", commands.slice(before).join("|") === "@puppet #1412", commands.slice(before).join("|"));
  check("the panel says what happened", queue.querySelector(".tk-fb")?.textContent?.includes("Claimed. Moved you to Lift Lobby.") === true);
  check("once it is ours the button stays, no longer the main one", (() => {
    const b = Array.from(queue.querySelectorAll<HTMLButtonElement>(".tk-btn")).find((x) => x.textContent?.trim() === "Puppet");
    return !!b && !b.classList.contains("primary");
  })());

  // A reply: Enter sends, the box clears, the answer shows.
  const box = queue.querySelector<HTMLTextAreaElement>(".tk-box")!;
  type(box, "Dov will take forty credits and a favor.");
  await settle();
  box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await wait(30);
  await settle();
  const replied = calls.filter((c) => c.action === "ticket_act").at(-1);
  check("Enter sends a reply through the request", replied?.data?.action === "reply" && replied?.data?.internal === false && replied?.data?.text === "Dov will take forty credits and a favor.", JSON.stringify(replied?.data));
  check("the box is cleared and the panel says it was sent", box.value === "" && queue.querySelector(".tk-fb")?.textContent?.includes("Reply sent.") === true, `${box.value}|${queue.querySelector(".tk-fb")?.textContent}`);

  // A note is its own tab, and goes as a note.
  queue.querySelectorAll<HTMLButtonElement>(".tk-tab")[1].click();
  await settle();
  check("the note tab changes what the box says it is for", queue.querySelector<HTMLTextAreaElement>(".tk-box")!.placeholder.includes("Only staff see it"), queue.querySelector<HTMLTextAreaElement>(".tk-box")!.placeholder);
  type(box, "Check the lift.");
  await settle();
  Array.from(queue.querySelectorAll<HTMLButtonElement>(".tk-btn")).find((b) => b.textContent?.trim() === "Save note")?.click();
  await wait(30);
  await settle();
  const noted = calls.filter((c) => c.action === "ticket_act").at(-1);
  check("a note goes as a note", noted?.data?.action === "reply" && noted?.data?.internal === true, JSON.stringify(noted?.data));
  queue.querySelectorAll<HTMLButtonElement>(".tk-tab")[0].click();
  await settle();

  // Saved replies are filled in for this ticket, then edited or sent.
  Array.from(queue.querySelectorAll<HTMLButtonElement>(".tk-btn")).find((b) => b.textContent?.trim() === "Saved replies")?.click();
  await wait(30);
  await settle();
  const item = queue.querySelector<HTMLButtonElement>(".tk-menu button");
  check("saved replies list by name", item?.textContent?.includes("thanks") === true);
  item?.click();
  await wait(30);
  await settle();
  check("choosing a saved reply puts its filled-in text in the box", queue.querySelector<HTMLTextAreaElement>(".tk-box")!.value === "Hi Mira Thane, this is #1042. Aliki", queue.querySelector<HTMLTextAreaElement>(".tk-box")!.value);
  type(queue.querySelector<HTMLTextAreaElement>(".tk-box")!, "");

  // Priority, close, and the rule that nothing types a command.
  const priority = queue.querySelector<HTMLSelectElement>(".tk-dh select")!;
  priority.value = "3";
  priority.dispatchEvent(new Event("change", { bubbles: true }));
  await wait(30);
  await settle();
  const pri = calls.filter((c) => c.action === "ticket_act").at(-1);
  check("priority goes through the request", pri?.data?.action === "priority" && pri?.data?.value === 3, JSON.stringify(pri?.data));
  const typedBefore = commands.length;
  Array.from(queue.querySelectorAll<HTMLButtonElement>(".tk-btn")).find((b) => b.textContent?.trim() === "Close")?.click();
  await wait(30);
  await settle();
  check("Close goes through the request", calls.filter((c) => c.action === "ticket_act").at(-1)?.data?.action === "close");
  check("Close types no command", commands.length === typedBefore, commands.slice(typedBefore).join("|"));

  // A player logging in moves their row up, live.
  tickets.handleOob("ticket_presence", [], { id: "clerk", presence: "room", seen: 0, sort: [0, 0, 0, now - 20000, 1000], account_online: true });
  await settle();
  check("a player arriving moves their ticket to the top without a reload", titles()[0] === "The tall clerk", titles().join("|"));

  // A new ticket for staff on duty raises a toast in plain words.
  toasts.list.forEach((t) => toasts.dismiss(t.id));
  tickets.handleOob("ticket_upsert", [], { ticket: row({ id: "n", ref: "#1050", label: "Puppet Request", kind: "puppet", title: "The ferryman", requester_name: "Joss", presence: "room", sort: [0, 0, 0, now, 1050] }), why: "new", notify: true });
  await settle();
  const toast = document.querySelector<HTMLElement>(".toast");
  check("a new ticket raises a toast that names it and where the player is", !!toast && toast.textContent!.includes("New puppet request #1050") && toast.textContent!.includes("Joss is in the room"), toast?.textContent ?? "none");
  toasts.list.forEach((t) => toasts.dismiss(t.id));

  // The keyboard.
  const root = queue.querySelector<HTMLElement>(".tk")!;
  tickets.closeStaff();
  await settle();
  queue.querySelector<HTMLElement>(".tk-list")!.dispatchEvent(new Event("focus"));
  root.dispatchEvent(new KeyboardEvent("keydown", { key: "j", bubbles: true }));
  await settle();
  check("j moves the cursor to the first row", queue.querySelectorAll(".tk-row.cursor").length === 1);
  root.dispatchEvent(new KeyboardEvent("keydown", { key: "/", bubbles: true }));
  await settle();
  check("/ goes to the search box", document.activeElement === queue.querySelector(".tk-search"));

  unmount(wide);
  queue.innerHTML = "";
  tickets.closeStaff();

  // ---- staff, a narrow panel ------------------------------------------------
  const narrow = document.getElementById("narrow")!;
  const slim = mount(TicketsPanel, { target: narrow });
  await settle();
  check("a narrow panel shows the list alone", shown(narrow.querySelector(".tk-pane-list")) && !shown(narrow.querySelector(".tk-pane-detail")));
  narrow.querySelectorAll<HTMLElement>(".tk-row")[1].click();
  await wait(30);
  await settle();
  check("opening a ticket takes the place of the list", !shown(narrow.querySelector(".tk-pane-list")) && shown(narrow.querySelector(".tk-pane-detail")));
  check("and offers a way back", shown(narrow.querySelector(".tk-back")));
  narrow.querySelector<HTMLButtonElement>(".tk-back")!.click();
  await settle();
  check("Back returns to the list", shown(narrow.querySelector(".tk-pane-list")) && !shown(narrow.querySelector(".tk-pane-detail")));
  check("a narrow panel sets no text below 12px either", smallText(narrow).length === 0, smallText(narrow).slice(0, 5).join(" | "));
  unmount(slim);
  narrow.innerHTML = "";

  // ---- a player: My requests -----------------------------------------------
  tickets.handleOob("ticket_role", [], { staff: false });
  const mine = document.getElementById("mine")!;
  mount(MyTicketsPanel, { target: mine });
  await settle();
  await wait(60);
  await settle();
  const mineRowsEls = () => Array.from(mine.querySelectorAll<HTMLElement>(".tk-row"));
  check("My requests lists the open ones, not the closed", mineRowsEls().length === 2, `${mineRowsEls().length}`);
  check("the request with a new reply is marked and says so in words", mineRowsEls()[0].classList.contains("new") && mineRowsEls()[0].textContent!.includes("Answered, new reply"), mineRowsEls()[0].textContent ?? "");
  check("each request carries a number a person can say", mineRowsEls()[0].textContent!.includes("#1043"));
  check("the player never sees an internal word", !/pending|waiting|with staff|short_id/i.test(mine.textContent ?? ""), mine.textContent ?? "");
  check("the player's panel sets no text below 12px", smallText(mine).length === 0, smallText(mine).slice(0, 5).join(" | "));

  const mineViews = Array.from(mine.querySelectorAll<HTMLButtonElement>(".tk-view"));
  check("the views are Open, Answered and All", mineViews.map((b) => b.textContent!.replace(/\d+/g, "").trim()).join("|") === "Open|Answered|All");
  mineViews[1].click();
  await settle();
  check("Answered shows only the requests staff have answered", mineRowsEls().length === 1 && mineRowsEls()[0].textContent!.includes("apartment door"), mineRowsEls().map((r) => r.textContent).join("|"));
  mineViews[0].click();
  await settle();

  mineRowsEls()[0].click();
  await wait(30);
  await settle();
  check("opening a request reads it and shows who wrote what", mine.querySelectorAll(".tk-msg").length === 2 && !!mine.querySelector(".tk-sys") && !!mine.querySelector(".tk-msg.staff"), `${mine.querySelectorAll(".tk-msg").length}`);
  check("reading it clears the new mark", tickets.myRows.find((r) => r.id === "m1")?.unread === false);
  check("the header says it is answered", mine.querySelector(".tk-ds")!.textContent!.includes("Answered"));

  const reply = mine.querySelector<HTMLTextAreaElement>(".tk-box")!;
  type(reply, "It is the east door, and the rent is paid.");
  await settle();
  reply.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await wait(30);
  await settle();
  const sent = calls.filter((c) => c.action === "my_ticket_act").at(-1);
  check("a reply goes through the request", sent?.data?.action === "reply" && sent?.data?.text === "It is the east door, and the rent is paid.", JSON.stringify(sent?.data));
  check("the panel says it was sent and clears the box", mine.querySelector(".tk-fb")?.textContent?.includes("Sent.") === true && reply.value === "");

  mine.querySelector<HTMLButtonElement>(".tk-hd .tk-btn")!.click(); // Back to the list
  await settle();
  // A staff reply to a request the player is not reading raises a toast.
  toasts.list.forEach((t) => toasts.dismiss(t.id));
  // The server keeps the unread mark, so the stub list does too.
  mineRows[1].unread = true;
  mineRows[1].status = "waiting";
  tickets.handleOob("ticket_msg", [], {
    id: "m2", ref: "#1042", label: "Puppet Request", title: "the scarred broker", text: "Dov will see you now.", sender: "Aliki", origin: "staff",
    audience: "owner", status: "waiting", ts: now, news: true,
  });
  await settle();
  const staffToast = document.querySelector<HTMLElement>(".toast");
  check("a staff reply with the request closed raises a toast", !!staffToast && staffToast.textContent!.includes("Staff replied: #1042 the scarred broker"), staffToast?.textContent ?? "none");
  check("and marks that request as new", mineRowsEls().some((r) => r.textContent!.includes("the scarred broker") && r.classList.contains("new")));
  toasts.list.forEach((t) => toasts.dismiss(t.id));

  // A new request: pick a kind, get help while writing, send.
  Array.from(mine.querySelectorAll<HTMLButtonElement>(".tk-btn")).find((b) => b.textContent?.trim() === "New request")?.click();
  await settle();
  const kinds = Array.from(mine.querySelectorAll<HTMLButtonElement>(".tk-pick button")).map((b) => b.firstElementChild?.textContent?.trim());
  check("the picker offers the four kinds in plain words", kinds.join("|") === "Ask staff a question|Report a bug|Report another player|Ask for an NPC to be puppeted", kinds.join("|"));
  mine.querySelector<HTMLButtonElement>(".tk-pick button")!.click();
  await settle();
  type(mine.querySelector<HTMLInputElement>(".tk-form input")!, "The east door");
  type(mine.querySelector<HTMLTextAreaElement>(".tk-form textarea")!, "It will not open and the rent is paid.");
  await wait(700);
  await settle();
  check("help pages that may answer it are suggested while writing", mine.querySelector(".tk-tips")?.textContent?.includes("help doors") === true, mine.querySelector(".tk-tips")?.textContent ?? "none");
  mine.querySelector<HTMLButtonElement>(".tk-tips button")!.click();
  check("a suggestion opens that help page", commands.includes("help doors"), commands.join("|"));
  mine.querySelector<HTMLButtonElement>(".tk-form .tk-btn.primary")!.click();
  await wait(30);
  await settle();
  const filed = calls.filter((c) => c.action === "ticket_open").at(-1);
  check("a new request is filed through the request, with its kind", filed?.data?.kind === "request" && filed?.data?.subject === "The east door", JSON.stringify(filed?.data));
  check("the panel confirms and shows the new request", mine.querySelector(".tk-fb")?.textContent?.includes("Request sent. Staff have been told.") === true && mine.querySelector(".tk-dt")?.textContent === "The east door", mine.querySelector(".tk-dt")?.textContent ?? "none");

  // The form's words are the game's. A bug form carries the advice on how bad, and a puppet form asks how to reach the character.
  mine.querySelector<HTMLButtonElement>(".tk-hd .tk-btn")!.click(); // Back to the list
  await settle();
  Array.from(mine.querySelectorAll<HTMLButtonElement>(".tk-btn")).find((b) => b.textContent?.trim() === "New request")?.click();
  await wait(30);
  await settle();
  check("the picker shows the question the game asked", mine.querySelector(".tk-pick .tk-note")?.textContent?.trim() === "What do you need today?", mine.querySelector(".tk-pick .tk-note")?.textContent ?? "none");
  check("the form was asked for from the game", calls.some((c) => c.action === "ticket_form"));
  mine.querySelectorAll<HTMLButtonElement>(".tk-pick button")[1].click(); // a bug
  await settle();
  const advice = mine.querySelector("#tk-severity-advice")?.textContent ?? "";
  check("a bug form tells a player to pick the lowest severity that fits", advice.includes("lowest one that fits"), advice);
  const severities = Array.from(mine.querySelectorAll<HTMLSelectElement>(".tk-form select")[1].options).map((o) => o.textContent);
  check("every severity says what it means, and lost items or money are not critical", severities.length === 5 && severities[4] === "Critical: the game stops, or an exploit" && !severities.join("").includes("money"), severities.join("|"));
  check("no severity but the lowest is chosen for the player", mine.querySelectorAll<HTMLSelectElement>(".tk-form select")[1].value === "Minor");
  mine.querySelector<HTMLButtonElement>(".tk-form .tk-btn.quiet")!.click(); // another kind
  await settle();
  mine.querySelectorAll<HTMLButtonElement>(".tk-pick button")[3].click(); // an NPC
  await settle();
  const note = mine.querySelector(".tk-form .tk-note")?.textContent ?? "";
  check("an NPC request says a player may leave and wait, and asks for contact information", note.includes("leave and wait") && note.includes("contact information"), note);
  check("and has a field for it", mine.querySelectorAll(".tk-form input").length === 2 && mine.querySelectorAll<HTMLInputElement>(".tk-form input")[1].placeholder.includes("handset ID"), `${mine.querySelectorAll(".tk-form input").length}`);
  type(mine.querySelectorAll<HTMLInputElement>(".tk-form input")[0], "the tall clerk");
  type(mine.querySelector<HTMLTextAreaElement>(".tk-form textarea")!, "I asked about the crate.");
  type(mine.querySelectorAll<HTMLInputElement>(".tk-form input")[1], "handset 5512");
  mine.querySelector<HTMLButtonElement>(".tk-form .tk-btn.primary")!.click();
  await wait(30);
  await settle();
  const npcFiled = calls.filter((c) => c.action === "ticket_open").at(-1);
  check("the contact information goes with the request", npcFiled?.data?.kind === "puppet" && npcFiled?.data?.contact === "handset 5512" && npcFiled?.data?.npc === "the tall clerk", JSON.stringify(npcFiled?.data));

  check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));
  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__ticketsTest = { done: true, failures, results };
}

run().catch((error) => {
  failures++;
  results.push(`FAIL harness crashed: ${error} ${error?.stack ?? ""}`);
  const pre = document.getElementById("results");
  if (pre) pre.textContent = results.join("\n");
  window.__ticketsTest = { done: true, failures, results };
});
