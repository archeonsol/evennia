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
import { notify } from "./notify.svelte";
import { puppets } from "./puppets.svelte";
import { routing } from "./routing.svelte";
import { scene } from "./scene.svelte";
import { session } from "./session.svelte";

/** This page has shown an account: a login, or a role push after a reload resumed one. */
let signedIn = false;

/** Empty every store that holds an account's lines, tickets, typing or pages. */
function forget(): void {
  activity.clear();
  chat.logout();
  commands.useAccount(null);
  compose.useAccount(null);
  routing.clearBuffers();
  puppets.clear();
  scene.reset();
  help.clear();
  notify.closeAll();
  dock.closeWebPages();
  // Sent while the socket is still open, so the portal drops its replay
  // window too and a reload cannot bring the scrollback back.
  session.clear();
  signedIn = false;
}

/**
 * An account signed in. A page still holding one never saw that session end,
 * so it is forgotten first. Otherwise the scrollback stays: a reload replays
 * this login with the lines that came after it.
 */
export function loggedIn(): void {
  if (signedIn) forget();
  chat.resetForLogin();
  signedIn = true;
}

/**
 * The handshake ended. One that could not resume a signed-in page means the
 * server ended that session: an idle timeout or a menu sign-out sends no
 * logout first.
 */
export function handshake(resumed: boolean): void {
  if (resumed || !signedIn) return;
  activity.logout();
  forget();
}

/** ticket_role names the account on every login and resync; history and the compose draft are kept under it. */
export function roleArrived(): void {
  commands.useAccount(chat.account);
  compose.useAccount(chat.account);
  if (chat.account != null) signedIn = true;
}

/** The server logged the account out (@quit, boot): forget it, then raise the quit screen. */
export function loggedOut(reason: string): void {
  activity.logout();
  forget();
  connection.markLoggedOut(reason);
}
