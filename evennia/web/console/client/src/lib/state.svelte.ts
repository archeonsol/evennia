/* Console state, and the half of it that lives in the address bar.
 *
 * Split in three deliberately.
 *
 * `view` is where the operator is. Every key in it round-trips through the URL,
 * because a view somebody reached by clicking must be reachable again by
 * pasting -- otherwise a bug report cannot contain the thing it is about.
 *
 * `session` is what the server said about itself. It is replaced on boot and on
 * every heartbeat, never edited by a panel.
 *
 * `local` is everything a link must not carry. See `URL_KEYS`.
 */

export interface PanelSummary {
  key: string;
  label: string;
  description?: string;
}

export interface Actor {
  name: string;
  id?: number;
}

/** The view keys, and nothing else, are carried in the address bar.
 *
 * Three groups stay out, each for its own reason.
 *
 * `trail` is how you walked here, not where you are.
 *
 * `replSource` and `sqlText` are operator input, and a URL is copied into chat
 * messages, tickets, and server logs. A pasted link must not be a way to leak a
 * query someone ran against production.
 *
 * `confirmed` is proof of presence. Putting it in a link would make the link
 * carry the presence check, which is the one thing it must never do.
 */
export const URL_KEYS = [
  "model",
  "cursor",
  "columns",
  "search",
  "order",
  "recordFilters",
  "attrModel",
  "attrSearch",
  "editing",
  "logFile",
  "logSearch",
  "errorState",
  "errorSearch",
  "errorOpen",
  "modState",
  "modFlag",
  "modAccount",
  "modKind",
  "modSeverity",
  "modSince",
  "modSearch",
  "modSignal",
  "authView",
  "objSearch",
  "actionSearch",
  "hookEvent",
  "hookSearch",
  "protoSearch",
  "auditOutcome",
  "auditPanel",
  "auditActor",
  "auditTarget",
  "auditCursor",
  "auditOpen",
  "attrObject",
  "attrKeyOpen",
  "jobStatus",
  "jobType",
  "jobOpen",
  "busPrefix",
  "busSubject",
  "busBefore",
  "busOpen",
  "dbModel",
  "viewPanel",
] as const;

export type ViewKey = (typeof URL_KEYS)[number];

export type View = Record<ViewKey, string>;

function emptyView(): View {
  const view = {} as View;
  for (const key of URL_KEYS) view[key] = "";
  return view;
}

export const view: View = $state(emptyView());

export const session = $state({
  panels: [] as PanelSummary[],
  current: "",
  degraded: false,
  version: "",
  actor: null as Actor | null,
  settings: {} as Record<string, boolean>,
  booted: false,
});

export const local = $state({
  /** The keyset cursors already passed, so the listing can step back.
   *
   * How you walked here, not where you are, which is why no link carries it:
   * a pasted URL lands on the page it names, with no history behind it. */
  trail: [] as string[],
  /** Operator input. A pasted link must never carry a query they ran. */
  replSource: "",
  sqlText: "",
  /** Proof of presence. A link must never carry the presence check. */
  confirmed: false,
  /** Rows ticked for a bulk operation, cleared whenever the listing changes. */
  chosen: [] as string[],
  /** Who else has this panel open. Read from the cache; never written. */
  presence: [] as { name: string; panel: string }[],
});

export const notice = $state({
  shown: false,
  kind: "fail" as "attn" | "fail",
  legend: "",
  detail: "",
});

export function clearNotice(): void {
  notice.shown = false;
}

export function showNotice(kind: "attn" | "fail", legend: string, detail: string): void {
  notice.kind = kind;
  notice.legend = legend;
  notice.detail = detail;
  notice.shown = true;
}

/** Read the panel key and view state out of the address bar. */
export function readUrl(): string {
  const hash = location.hash.replace(/^#/, "");
  const [panel, query] = hash.split("?");
  const params = new URLSearchParams(query || "");
  for (const key of URL_KEYS) {
    if (params.has(key)) view[key] = params.get(key) || "";
  }
  return panel || "";
}

/** Write the current panel and view state back to the address bar. */
export function writeUrl(): void {
  const params = new URLSearchParams();
  for (const key of URL_KEYS) {
    if (view[key]) params.set(key, view[key]);
  }
  const query = params.toString();
  const next = `#${session.current || ""}${query ? "?" + query : ""}`;
  if (next !== location.hash) history.replaceState(null, "", next);
}

/**
 * Move to one panel.
 *
 * View state is *not* cleared. A pasted deep link sets it before the panel is
 * selected, and clearing here would discard the thing the link was about.
 *
 * Arriving at Records is the one exception: it drops the keyset cursor and the
 * trail behind it, because a cursor from a previous visit points into a page of
 * a listing the operator is no longer looking at.
 */
export function select(key: string): void {
  if (session.current !== key && key === "records") {
    view.cursor = "";
    local.trail = [];
  }
  session.current = key;
  local.chosen = [];
  writeUrl();
}
