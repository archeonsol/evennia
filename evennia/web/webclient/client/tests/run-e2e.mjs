// End to end: the whole shell in a real Chromium, against a fake portal.
//
//   npm run test:e2e
//
// The saved log is the part of the shell that only means anything across page
// loads, so it cannot be checked in one page. This script loads the shell the
// way the game serves it (tests/shell.html), answers its WebSocket with a small
// model of the Portal, and then does what players do to it: reloads it, closes
// the tab, restarts the Portal under it, signs another account in, opens a
// second tab. Each check reads what the player would see (the scrollback) and
// what the browser kept (the IndexedDB), never the stores' internals.
//
// The fake portal models only what the saved log depends on: sessions held by
// resume token, sequence numbers on every frame, a bounded replay window,
// `hello` carrying `resumed`/`token`/`account`/`ts`, and a restart that forgets
// every session. It is not a Portal and tests nothing about one; the Portal's
// own side is in evennia/server/portal/test_webclient_resume.py.

import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const PORTAL = /portal\.test/;
const REPLAY_FRAMES = 400;

// -- the fake portal ---------------------------------------------------------

class Session {
  constructor(account) {
    this.account = account;
    this.seq = 0;
    this.buffer = [];
    this.sockets = new Set();
    this.token = null;
  }

  /** Stamp a frame the way the Portal does, keep it for replay, and send it. */
  push(frame) {
    const stamped = { ...frame, s: ++this.seq, ts: Date.now() };
    const line = JSON.stringify(stamped);
    this.buffer.push([stamped.s, line]);
    if (this.buffer.length > REPLAY_FRAMES) this.buffer.shift();
    for (const ws of this.sockets) ws.send(line);
  }
}

const oob = (event, kwargs = {}, args = []) => ({ t: "oob", event, args, kwargs });

class FakePortal {
  constructor() {
    this.sessions = new Set();
    this.byToken = new Map();
    /** The account the browser is signed in as, as its Django session says. */
    this.browserAccount = 7;
    /** Every hello the shell has sent. */
    this.hellos = [];
    /** What a brand new session is told, besides the login. */
    this.welcome = ["Welcome to the game."];
  }

  attach(ws) {
    const mine = { session: null };
    ws.onMessage((raw) => this.receive(ws, mine, JSON.parse(String(raw))));
    ws.onClose(() => {
      mine.session?.sockets.delete(ws);
    });
  }

  receive(ws, mine, msg) {
    if (msg.t === "hello") return this.hello(ws, mine, msg);
    if (msg.t === "ping") return ws.send(JSON.stringify({ t: "pong", n: msg.n }));
    if (msg.t === "req") return ws.send(JSON.stringify({ t: "res", re: msg.seq, ok: false, error: "unsupported" }));
    if (msg.t === "resume_reset") mine.session && (mine.session.buffer = []);
  }

  hello(ws, mine, msg) {
    this.hellos.push(msg);
    const { token, last_seq: lastSeq = 0, replay } = msg.resume ?? {};
    const held = token ? this.byToken.get(token) : undefined;
    const account = this.browserAccount;
    const resumed = !!held && account !== null && held.account === account;
    let session;
    if (resumed) {
      session = held;
      this.byToken.delete(token);
      if (replay !== false) {
        for (const [s, line] of session.buffer) if (s > lastSeq) ws.send(line);
      }
    } else {
      session = new Session(account);
      this.sessions.add(session);
    }
    mine.session = session;
    session.token = randomBytes(12).toString("hex");
    this.byToken.set(session.token, session);
    // Stamped last, so its seq is above every frame replayed before it.
    session.sockets.add(ws);
    session.push({ t: "hello", protocol: "azaban.v1", resumed, token: session.token, account });
    if (account !== null) {
      // What the game says on a login and on every resync.
      if (!resumed) {
        session.push(oob("logged_in"));
        for (const text of this.welcome) session.push({ t: "text", html: text, kind: "text" });
      }
      session.push(oob("ticket_role", { staff: false, account }));
    } else {
      session.push({ t: "text", html: "Please sign in.", kind: "text" });
    }
  }

  /** The game says a line to everyone signed in as `account`. */
  say(text, account = this.browserAccount) {
    for (const session of this.sessions) {
      if (session.account === account) session.push({ t: "text", html: text, kind: "text" });
    }
  }

  /** The browser signs in at the connection screen: the open socket learns who it is. */
  signIn(account) {
    this.browserAccount = account;
    for (const session of this.sessions) {
      if (session.account !== null) continue;
      session.account = account;
      session.push(oob("logged_in"));
      session.push({ t: "text", html: "Welcome to the game.", kind: "text" });
      session.push(oob("ticket_role", { staff: false, account }));
    }
  }

  /** The player quits: the game says so, and the session is over for good. */
  quit(account = this.browserAccount) {
    for (const session of [...this.sessions]) {
      if (session.account !== account) continue;
      session.push(oob("logout", {}, ["quit"]));
      this.sessions.delete(session);
      this.byToken.delete(session.token);
    }
  }

  /** The Portal process is restarted: every socket drops and every session is forgotten. */
  restart() {
    const sockets = [...this.sessions].flatMap((s) => [...s.sockets]);
    this.sessions.clear();
    this.byToken.clear();
    for (const ws of sockets) ws.close({ code: 1012, reason: "restart" });
  }
}

// -- the browser -------------------------------------------------------------

async function launch() {
  const attempts = [
    ...(process.env.BROWSER_PATH ? [{ executablePath: process.env.BROWSER_PATH }] : []),
    { channel: "msedge" },
    { channel: "chrome" },
    { executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" },
    { executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" },
    { executablePath: "/usr/bin/microsoft-edge" },
    { executablePath: "/usr/bin/google-chrome" },
  ];
  const errors = [];
  for (const attempt of attempts) {
    try {
      return await chromium.launch(attempt);
    } catch (error) {
      errors.push(`${JSON.stringify(attempt)}: ${error.message.split("\n")[0]}`);
    }
  }
  throw new Error(`no Chromium-family browser found:\n  ${errors.join("\n  ")}`);
}

const server = await createServer({
  configFile: fileURLToPath(new URL("../vite.config.ts", import.meta.url)),
  server: { host: "127.0.0.1", port: 5198, strictPort: false },
  logLevel: "warn",
});
await server.listen();
const base = server.resolvedUrls?.local?.[0];
if (!base) throw new Error("vite did not report a local URL");
const SHELL = new URL("tests/shell.html", base).href;

const portal = new FakePortal();
const browser = await launch();
const context = await browser.newContext();
await context.routeWebSocket(PORTAL, (ws) => portal.attach(ws));

const pageErrors = [];
const watch = (page) => {
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error" && !/Failed to load resource|ERR_/.test(message.text())) {
      pageErrors.push(message.text());
    }
  });
};

let failures = 0;
function check(name, ok, detail = "") {
  if (!ok) failures++;
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${!ok && detail ? `: ${detail}` : ""}`);
}

async function until(read, ok, what, ms = 15_000) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) {
    last = await read();
    if (ok(last)) return last;
    await new Promise((r) => setTimeout(r, 50));
  }
  console.log(`  (timed out waiting for ${what}; last: ${JSON.stringify(last)?.slice(0, 300)})`);
  return last;
}

/** What the player has in the scrollback, oldest first. */
const shown = (page) =>
  page.evaluate(async () => (await import("/src/lib/session.svelte.ts")).session.lines.map((l) => l.text));

/** What the browser has kept for an account, oldest first. */
const kept = (page, account) =>
  page.evaluate(
    (account) =>
      new Promise((resolve, reject) => {
        const open = indexedDB.open("underspire.scrollback");
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const db = open.result;
          if (!db.objectStoreNames.contains("lines")) {
            db.close();
            resolve([]);
            return;
          }
          const q = db
            .transaction("lines")
            .objectStore("lines")
            .getAll(IDBKeyRange.bound([account, 0], [account, Number.MAX_SAFE_INTEGER]));
          q.onsuccess = () => {
            db.close();
            resolve(q.result.map((r) => r.text));
          };
          q.onerror = () => reject(q.error);
        };
      }),
    account,
  );

/** With E2E_SHOTS=<dir>, keep a picture of the page at the points a person would look at it. */
async function shot(page, name) {
  if (!process.env.E2E_SHOTS) return;
  // Any key skips the boot screen.
  await page.keyboard.press("Shift");
  await new Promise((r) => setTimeout(r, 300));
  await page.screenshot({ path: `${process.env.E2E_SHOTS}/${name}.png` });
}

const NOTE_LAST_VISIT = "Lines above are from your last visit.";
const NOTE_AWAY = "You were disconnected. Output from that time is missing.";
const NOTE_SAVING = "Your scrollback is now saved on this device. You can turn that off in Settings, under Backup.";
const has = (list, text) => list.includes(text);
const count = (list, text) => list.filter((t) => t === text).length;

async function open(page) {
  await page.goto(SHELL, { waitUntil: "commit" });
}

// -- scenarios ---------------------------------------------------------------

async function main() {
  const page = await context.newPage();
  watch(page);

  // 1. A first visit.
  await open(page);
  await until(() => shown(page), (l) => has(l, "Welcome to the game."), "the welcome");
  portal.say("Mira says, 'hello'.");
  portal.say("Mira waves.");
  portal.say("A bell rings.");
  let log = await until(() => shown(page), (l) => has(l, "A bell rings."), "three lines");
  check("a first visit shows what the game says", has(log, "Mira waves."), JSON.stringify(log));
  check("a new device is told that it keeps the log", has(log, NOTE_SAVING), JSON.stringify(log));
  let disk = await until(() => kept(page, 7), (l) => has(l, "A bell rings."), "the lines to be kept");
  check("and keeps it on the device", has(disk, "Mira says, 'hello'.") && has(disk, "A bell rings."), JSON.stringify(disk));
  check("each line once", count(disk, "Mira waves.") === 1);

  // 2. A reload, with a line said while the page was away.
  await page.goto("about:blank");
  portal.say("The rain starts.");
  const helloCount = portal.hellos.length;
  await open(page);
  log = await until(() => shown(page), (l) => has(l, "The rain starts.") && has(l, "A bell rings."), "the log after a reload");
  const reload = portal.hellos[helloCount];
  check("a reload brings the log back", has(log, "Mira says, 'hello'.") && has(log, "A bell rings."), JSON.stringify(log));
  check("with the line said meanwhile", has(log, "The rain starts."));
  check("no line twice", ["Welcome to the game.", "Mira waves.", "A bell rings.", "The rain starts."].every((t) => count(log, t) === 1), JSON.stringify(log));
  check("in the order it happened", log.indexOf("A bell rings.") < log.indexOf("The rain starts."));
  check("with no break marked, since nothing broke", !has(log, NOTE_LAST_VISIT) && !has(log, NOTE_AWAY));
  check("and it is told only once", count(log, NOTE_SAVING) === 1, JSON.stringify(log));
  check("the portal was asked only for the gap", reload.resume.last_seq > 0 && reload.resume.replay === undefined, JSON.stringify(reload.resume));
  disk = await until(() => kept(page, 7), (l) => has(l, "The rain starts."), "the new line to be kept");
  check("the device holds each line once", ["Mira waves.", "A bell rings.", "The rain starts."].every((t) => count(disk, t) === 1), JSON.stringify(disk));

  // 2b. Leaving the page the instant a line lands: the unload may or may not finish
  // its write, and either way the line is there once, not lost and not doubled.
  for (let n = 1; n <= 4; n++) {
    const line = `just before leaving ${n}`;
    portal.say(line);
    await until(() => shown(page), (l) => has(l, line), `the page to show ${line}`);
    await page.goto("about:blank");
    await open(page);
    log = await until(() => shown(page), (l) => has(l, line), `the log to have ${line} after leaving`);
    check(`a line said as the page goes away is kept once (${n})`, count(log, line) === 1, JSON.stringify(log.slice(-4)));
  }
  await until(() => kept(page, 7), (l) => has(l, "just before leaving 4"), "the last line to be kept");
  disk = await kept(page, 7);
  check("and on the device once each", [1, 2, 3, 4].every((n) => count(disk, `just before leaving ${n}`) === 1), JSON.stringify(disk.slice(-6)));

  // 3. A reload that also finds the Portal restarted.
  await page.goto("about:blank");
  portal.restart();
  await open(page);
  log = await until(() => shown(page), (l) => has(l, NOTE_LAST_VISIT), "the log after a restart");
  check("a restart does not take the log", has(log, "A bell rings.") && has(log, "The rain starts."), JSON.stringify(log));
  check("the break is marked after it", log.indexOf(NOTE_LAST_VISIT) > log.indexOf("The rain starts."), JSON.stringify(log));
  check("and the new session follows the mark", log.indexOf("Welcome to the game.", log.indexOf(NOTE_LAST_VISIT)) > 0, JSON.stringify(log));
  check("nothing doubled by the restart", count(log, "A bell rings.") === 1);
  await new Promise((r) => setTimeout(r, 600));
  await shot(page, "1-restored-with-note");

  // 4. Another account on the same browser.
  await page.goto("about:blank");
  portal.browserAccount = 9;
  await open(page);
  log = await until(() => shown(page), (l) => has(l, "Welcome to the game."), "account 9's welcome");
  check("another account sees none of it", !has(log, "A bell rings.") && !has(log, "Mira waves."), JSON.stringify(log));
  portal.say("Nine's own line.");
  await until(() => kept(page, 9), (l) => has(l, "Nine's own line."), "account 9's line to be kept");
  check("and keeps its own apart", !has(await kept(page, 9), "A bell rings."));
  check("the first account's log is still there", has(await kept(page, 7), "A bell rings."));

  // 5. Back to the first account.
  await page.goto("about:blank");
  portal.browserAccount = 7;
  await open(page);
  log = await until(() => shown(page), (l) => has(l, NOTE_LAST_VISIT), "account 7's log");
  check("the first account gets its log back", has(log, "A bell rings.") && !has(log, "Nine's own line."), JSON.stringify(log));

  // 6. Signed out, then signing in at the connection screen.
  await page.goto("about:blank");
  portal.browserAccount = null;
  await open(page);
  log = await until(() => shown(page), (l) => has(l, "Please sign in."), "the sign-in screen");
  await new Promise((r) => setTimeout(r, 600));
  check("a signed-out browser sees none of it", !has(await shown(page), "A bell rings."), JSON.stringify(await shown(page)));
  check("and nothing of the sign-in screen is kept", !has(await kept(page, 7), "Please sign in."));
  portal.signIn(7);
  log = await until(() => shown(page), (l) => has(l, NOTE_LAST_VISIT) && has(l, "A bell rings."), "the log after signing in");
  check("signing in brings the account's log back", has(log, "A bell rings."), JSON.stringify(log));
  check("in front of the sign-in", log.indexOf("A bell rings.") < log.indexOf(NOTE_LAST_VISIT));

  // 7. A Portal restart under a page that stays open.
  const before = await shown(page);
  portal.restart();
  log = await until(() => shown(page), (l) => has(l, NOTE_AWAY), "the page to reconnect", 20_000);
  check("a restart under an open page keeps its log", before.every((t) => has(log, t)), JSON.stringify(log));
  check("and says the connection broke", has(log, NOTE_AWAY));
  portal.say("After the restart.");
  disk = await until(() => kept(page, 7), (l) => has(l, "After the restart."), "the new line to be kept");
  check("the lines after it are kept in order", disk.indexOf(NOTE_AWAY) > disk.indexOf("A bell rings.") && disk.indexOf("After the restart.") > disk.indexOf(NOTE_AWAY), JSON.stringify(disk));

  // 8. Quitting deletes the saved copy.
  portal.quit(7);
  await until(() => kept(page, 7), (l) => l.length === 0, "the saved log to go");
  check("quitting deletes the saved log", (await kept(page, 7)).length === 0);
  await page.goto("about:blank");
  await open(page);
  log = await until(() => shown(page), (l) => has(l, "Welcome to the game."), "a fresh start");
  check("and the next visit starts clean", !has(log, "A bell rings."), JSON.stringify(log));

  // 9. A long log: one screenful back, the rest on request, the reader kept in place.
  await page.goto("about:blank");
  portal.browserAccount = 11;
  await open(page);
  await until(() => shown(page), (l) => has(l, "Welcome to the game."), "account 11's welcome");
  const total = 6200;
  for (let i = 1; i <= total; i++) portal.say(`record ${i}`, 11);
  await until(() => shown(page), (l) => has(l, `record ${total}`), "the long log", 90_000);
  await until(() => kept(page, 11), (l) => has(l, `record ${total}`), "the long log to be kept", 60_000);
  await page.goto("about:blank");
  portal.restart();
  await open(page);
  log = await until(() => shown(page), (l) => has(l, NOTE_LAST_VISIT), "the long log after a restart", 30_000);
  check("a long log comes back one screenful at a time", log.length > 4900 && log.length < 5300, `${log.length} lines`);
  check("the newest lines are the ones shown", has(log, `record ${total}`));
  const earlier = page.getByRole("button", { name: "load earlier lines" });
  check("older lines are offered", (await earlier.count()) === 1);
  await shot(page, "2-earlier-offered");
  const rows = () =>
    page.evaluate(() => {
      const el = document.querySelector(".game-log");
      const top = el.getBoundingClientRect().top;
      const row = [...el.querySelectorAll(".log-line")].find((r) => r.getBoundingClientRect().bottom > top + 0.5);
      return row ? { text: row.textContent, offset: row.getBoundingClientRect().top - top } : null;
    });
  await page.evaluate(() => {
    const el = document.querySelector(".game-log");
    el.dispatchEvent(new WheelEvent("wheel", { deltaY: -120, bubbles: true }));
    el.scrollTop = 0;
    el.dispatchEvent(new Event("scroll"));
  });
  await until(() => shown(page), (l) => l.length > 5400, "an older page to load");
  await new Promise((r) => setTimeout(r, 400));
  const after = await shown(page);
  check("scrolling to the top reads the next page", after.length > 5400, `${after.length} lines`);
  const place = await rows();
  check("and the reader is not thrown to the top", place !== null && (await page.evaluate(() => document.querySelector(".game-log").scrollTop)) > 200, JSON.stringify(place));
  check("older lines are in front, in order", after[500] === log[0] && after[499] !== undefined && Number(after[499].split(" ")[1]) === Number(log[0].split(" ")[1]) - 1, `${after[499]} / ${after[500]}`);

  // 10. Two tabs of one account.
  await page.close();
  portal.browserAccount = 7;
  portal.welcome = ["Welcome to the game."];
  const a = await context.newPage();
  watch(a);
  await open(a);
  await until(() => shown(a), (l) => has(l, "Welcome to the game."), "tab A's welcome");
  const b = await context.newPage();
  watch(b);
  await open(b);
  await until(() => shown(b), (l) => has(l, "Welcome to the game."), "tab B's welcome");
  portal.say("Both tabs hear this.");
  await until(() => shown(a), (l) => has(l, "Both tabs hear this."), "tab A to hear it");
  await until(() => shown(b), (l) => has(l, "Both tabs hear this."), "tab B to hear it");
  disk = await until(() => kept(a, 7), (l) => has(l, "Both tabs hear this."), "the line to be kept");
  await new Promise((r) => setTimeout(r, 800));
  disk = await kept(a, 7);
  check("two tabs keep a line once", count(disk, "Both tabs hear this.") === 1, JSON.stringify(disk.slice(-5)));
  await a.close();
  await new Promise((r) => setTimeout(r, 300));
  portal.say("Only the second tab is left.");
  disk = await until(() => kept(b, 7), (l) => has(l, "Only the second tab is left."), "the second tab to take over saving");
  check("the second tab carries on saving when the first closes", has(disk, "Only the second tab is left."), JSON.stringify(disk.slice(-5)));
  await b.close();

  // 11. The player's own switch.
  const c = await context.newPage();
  watch(c);
  await open(c);
  await until(() => shown(c), (l) => has(l, "Welcome to the game."), "the welcome");
  await c.keyboard.press("Control+,");
  await c.getByText("Backup", { exact: true }).first().click();
  await new Promise((r) => setTimeout(r, 400));
  await shot(c, "3-settings-backup");
  await c.keyboard.press("Escape");
  await c.keyboard.press("Escape");
  await c.evaluate(async () => {
    const { savedLog } = await import("/src/lib/savedlog.svelte.ts");
    const { settings } = await import("/src/lib/settings.svelte.ts");
    settings.rememberLog = false;
    await savedLog.setEnabled(false);
  });
  check("turning saving off deletes what was saved", (await kept(c, 7)).length === 0);
  portal.say("Not kept.");
  await until(() => shown(c), (l) => has(l, "Not kept."), "the line");
  await new Promise((r) => setTimeout(r, 900));
  check("and nothing more is kept", (await kept(c, 7)).length === 0);
  await c.close();

  check("no errors on any page", pageErrors.length === 0, pageErrors.slice(0, 3).join(" | "));
}

let code = 0;
try {
  await main();
  if (failures) code = 1;
  console.log(`\n${failures ? `${failures} failed` : "all passed"}`);
} catch (error) {
  console.error(error);
  code = 1;
} finally {
  await browser.close();
  await server.close();
}
process.exit(code);
