# Azaban: the saved log

The log a player has read, kept in the browser so a reload, a closed tab, a phone
that was put away or a restart of the game does not take it away. Part of the
runtime contract in [`webclient-protocol-runtime.md`](webclient-protocol-runtime.md);
the Portal half (hold, takeover, replay) is in
[`webclient-session-continuity.md`](webclient-session-continuity.md).

Code, in `evennia/web/webclient/client/src/lib/`: `logstore.ts` (the IndexedDB
database), `savedlog.svelte.ts` (what to save, when to show it),
`session.svelte.ts` (the record it feeds and restores), `accountSession.ts` (who
is signed in), and the connection store (the cursor), at
`evennia/web/webclient/client/src/lib/evennia.svelte.ts`.

## Why it exists

The Portal's replay buffer is 400 frames in memory. On its own it cannot bring a
log back after the hold ran out (15 seconds for a reload or a closed tab, 90 for a
lost link) or after the Portal restarted, and a reload that does reach it is sent
frames, not 5,000 lines. Before the saved log a handshake that could not resume also
emptied a screen the tab still held, so a laptop that slept, or any full restart,
wiped a log that was never gone.

## What is stored

One IndexedDB database (`underspire.scrollback`), two stores:

- `lines`, one row per log line under `[account, k]`: the markup, the plain text,
  the type and the time. `k` is counted up inside the transaction that writes the
  row, and lines only ever leave from the old end, so the keys of one account are
  one unbroken run. "The last N lines" and "the N lines before this one" are each
  one range read.
- `meta`, one row per account: where the run starts and ends, its size, when it
  was last written, and the place the connection had reached (`tab`, `seq`).

## The rules

Each is wrong under the obvious implementation.

- **The lines and the place they reach are written together.** One transaction
  writes both, and the two are taken in one step before anything is awaited. A
  reloaded page presents that `seq` as `resume.last_seq`, so the Portal sends the
  gap and nothing else: no line twice, none missing. Saving them separately cannot
  have this property.
- **Only the tab that wrote the place may use it.** `tab` is a name in the tab's
  `sessionStorage`. A page that finds the place written by another tab resumes with
  `replay: false` and is sent nothing: the saved lines are the other tab's.
- **A log is shown to its account, after the handshake.** Nothing is shown on page
  load. The `hello` reply names the account the browser is signed in as, and only
  that account's log is put in front of the lines of this visit. A sign-in screen
  shows nothing and saves nothing printed before login. The command history
  follows the same rule, so the next person to sit down finds nothing of the last
  account.
- **The same person coming back keeps the page's own log.** A handshake that could
  not resume empties the screen only when `hello.account` is not the account the
  page held. The same account keeps its log, with a line saying the connection
  broke. A Portal too old to send `account` keeps the old rule: empty it.
- **One tab saves at a time per account**, by a Web Lock named for the account. A
  second tab shows its own log as usual, waits, and saves from the moment the
  first lets go. Of what it held meanwhile it saves only lines newer than the last
  one saved: the rest is a second copy. The lock is let go after the write on its
  way finishes, so the next tab's lines cannot land in front of it.
- **It is a cache.** If the database cannot be opened, is full or is cleared, the
  page keeps its log in memory exactly as before. A page waits at most 1.5 seconds
  for the database before it connects without it, and starts saving if it comes up.
  A write that fails three times in a row stops saving for that page.

## Limits

20,000 lines or about 24 MB per account, trimmed from the old end in blocks; three
accounts per browser, the least recently written going first; a log nobody has
written to for 7 days is deleted unread; a log saved under another markup version
(`SCHEMA`) is dropped, not shown wrongly. Memory still holds 5,000 lines, up to
25,000 while the reader is away from the bottom and has read older ones back.

A burst of 500 waiting lines is written at once, not after the 400 ms pause.
Nothing waits for more than 5,000 lines.

## What is never saved

Anything before sign-in; media embeds; the echo of a command that carries a
password (`connect`, `create`, `@password` and the like); a line over 200 KB. The
staff Activity feed and NPC terminals are not in the scrollback and so are not
here.

## What the player sees and chooses

- A reload or a restart shows the log, and a line after it: "Lines above are from
  your last visit." A connection that broke under an open page shows "You were
  disconnected. Output from that time is missing." Neither is shown for a session
  that resumed, since nothing broke. A device that has not been told is told once,
  in the log, that it now keeps the scrollback and how to stop that. All three are
  in `NOTES`, one place to reword.
- "Earlier" in the log's toolbar, or scrolling to the top, reads the next 500
  saved lines in front of the ones held. The reader stays on the line they were on.
- Settings, Backup: "Save scrollback on this device" (on by default) and "Delete
  saved scrollback". Turning saving off deletes what was saved.
- The copy stays on the device for its account across a sign-out, a closed tab and
  an idle timeout. Quitting, being booted, and the log's Clear delete it.
- The pointer to the last account (`underspire.log.last`) and the told flag
  (`underspire.log.told`), both in `localStorage`, are device state and are not in
  a config export.

## Tests

`logstore.test.ts` (the database and its limits), `savedlog.test.ts` (show, save,
tabs, older pages, the player's choices), `connection.test.ts`,
`accountSession.test.ts`, and `npm run test:e2e`: the whole shell in a browser
against a fake Portal that holds sessions by token and replays by seq, through
reloads, restarts, other accounts, a sign-in, a quit and two tabs.
