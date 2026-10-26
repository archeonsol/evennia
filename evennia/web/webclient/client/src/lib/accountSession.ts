// The signed-in account's place in the shell: what a login, a role push and
// the end of a session do to the stores. A shared browser is the case that
// matters: the next person to sit down must find nothing of the last account.

import { activity } from "./activity.svelte";
import { chat } from "./chat.svelte";
import { commands } from "./commands.svelte";
import { compose } from "./compose.svelte";
import { dock } from "./dock.svelte";
import { connection } from "./evennia.svelte";
import { help } from "./help.svelte";
import { lore } from "./lore.svelte";
import { notify } from "./notify.svelte";
import { puppets } from "./puppets.svelte";
import { puppetLayout } from "./puppetLayout.svelte";
import { routing } from "./routing.svelte";
import { NOTES, noteHtml, savedLog } from "./savedlog.svelte";
import { scene } from "./scene.svelte";
import { session } from "./session.svelte";

/** This page has shown an account: a login, or a role push after a reload resumed one. */
let signedIn = false;

/** The account whose lines this page holds, once the Portal or the game has said which. */
let held: number | null = null;

/**
 * Empty every store that holds an account's lines, tickets, typing or pages.
 *
 * With `keepLog` the scrollback, and the feeds that are cut from it, stay: the
 * same person is back and the game will say everything else again.
 */
function forget(keepLog = false): void {
  activity.clear();
  chat.logout();
  commands.useAccount(null);
  compose.useAccount(null);
  puppets.clear();
  puppetLayout.useAccount(null);
  scene.reset();
  help.clear();
  lore.hide(true);
  notify.closeAll();
  dock.closeWebPages();
  if (!keepLog) {
    routing.clearBuffers();
    // What was waiting to be saved goes out under this account first. The copy
    // on this device stays, for the account to find when it comes back.
    savedLog.signedOut();
    // Sent while the socket is still open, so the portal drops its replay
    // window too and a reload cannot bring the scrollback back.
    session.clear();
    held = null;
  }
  signedIn = false;
}

/**
 * An account signed in. A page still holding one never saw that session end,
 * so it is forgotten first. Otherwise the scrollback stays: a reload replays
 * this login with the lines that came after it.
 */
export function loggedIn(): void {
  if (signedIn) forget();
  // Whatever was printed before this belongs to the sign-in screen.
  savedLog.discardUnbound();
  chat.resetForLogin();
  signedIn = true;
}

/**
 * The handshake ended, and the Portal said whether it could resume the session
 * and who the browser is signed in as.
 *
 * A handshake that could not resume a signed-in page means the server ended
 * that session: an idle timeout or a menu sign-out sends no logout first, and so
 * does a restart of the Portal. Which of those it was shows in who comes back.
 * The same account: the same person, whose own screen it is, and the log stays
 * with a line saying the connection broke. No account, or another one: the next
 * person to sit down, and the screen is emptied.
 *
 * Args:
 *   resumed: the Portal resumed the session and replayed what was missed.
 *   account: the account the browser is signed in as, null at the sign-in
 *     screen. Undefined from a Portal too old to say, which is treated as the
 *     old rule: nothing is known, so the screen is emptied.
 */
export function handshake(resumed: boolean, account?: number | null): void {
  const known = account !== undefined;
  const who = account ?? null;
  if (resumed || !signedIn) {
    if (known) {
      if (who !== null) held = who;
      void savedLog.hello({ resumed, account: who, kept: false });
    }
    return;
  }
  activity.logout();
  if (known && who !== null && who === held) {
    forget(true);
    session.append(noteHtml(NOTES.away), "system");
    void savedLog.hello({ resumed: false, account: who, kept: true });
    return;
  }
  forget();
  if (known) {
    held = who;
    void savedLog.hello({ resumed: false, account: who, kept: false });
  }
}

/** ticket_role names the account on every login and resync; history and the compose draft are kept under it. */
export function roleArrived(): void {
  commands.useAccount(chat.account);
  compose.useAccount(chat.account);
  puppetLayout.useAccount(chat.account);
  if (chat.account != null) {
    signedIn = true;
    held = chat.account;
    void savedLog.signedIn(chat.account);
  }
}

/**
 * The server logged the account out (@quit, boot): forget it, then raise the quit screen.
 *
 * A player who quit, or was removed, has ended the visit, so the log saved on
 * this device goes too. Any other reason (the session moved to another window)
 * leaves it: the account is still in use.
 */
export function loggedOut(reason: string): void {
  if (reason === "quit" || reason === "booted") void savedLog.erase();
  activity.logout();
  forget();
  connection.markLoggedOut(reason);
}
