/* Engine console client.
 *
 * Plain ES modules, no build step and no dependencies. That is a deliberate
 * choice rather than a shortcut: the console renders a station rail and one
 * panel at a time, the engine promises that a game never runs npm, and a
 * committed build artifact rots. Revisit if a station needs more than the DOM
 * can carry plainly.
 *
 * Copy in this file follows Simplified Technical English: one meaning per
 * word, one instruction per sentence, active voice, and the same word for the
 * same thing every time.
 */

const API = "/api/console/";
const HEADER = "X-Evennia-Console";

const el = {
  rail: document.getElementById("rail"),
  station: document.getElementById("station"),
  lamps: document.getElementById("strip-lamps"),
  version: document.getElementById("strip-version"),
  actor: document.getElementById("strip-actor"),
  notice: document.getElementById("notice"),
};

const state = {
  panels: [],
  current: null,
  degraded: false,
  model: "",
  cursor: "",
  trail: [],
  columns: "",
  search: "",
  order: "",
  attrModel: "",
  attrSearch: "",
  editing: null,
  logFile: "",
  logSearch: "",
  errorState: "",
  errorSearch: "",
  errorOpen: null,
  modState: "",
  modFlag: null,
  authView: "grants",
  probe: null,
  replSource: "",
  sqlText: "",
  confirmed: false,
  objSearch: "",
  actionSearch: "",
  actionProbe: null,
  hookEvent: "",
  hookSearch: "",
  protoSearch: "",
  auditOutcome: "",
  auditPanel: "",
  auditActor: "",
  auditTarget: "",
  auditCursor: "",
  auditOpen: null,
  attrObject: "",
  attrKeyOpen: "",
  jobStatus: "",
  jobType: "",
  jobOpen: null,
  busPrefix: "",
  busSubject: "",
  busBefore: "",
  busOpen: null,
  dbModel: "",
  viewPanel: "",
  chosen: [],
  presence: [],
  live: { source: null, health: null, metrics: null, log: [] },
};

/* Keys carried in the address bar. A view an operator reached by clicking must
 * be reachable again by pasting, or a bug report cannot contain the thing it
 * is about.
 *
 * Three groups stay out, each for its own reason.
 *
 * `trail` is how you walked here, not where you are.
 *
 * `replSource` and `sqlText` are operator input, and a URL is copied into chat
 * messages, tickets, and server logs. A pasted link must not be a way to leak
 * a query someone ran against production.
 *
 * `confirmed` is proof of presence. Putting it in a link would make the link
 * carry the presence check, which is the one thing it must never do. */
const URL_KEYS = [
  "model",
  "cursor",
  "columns",
  "search",
  "order",
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
];

function readUrl() {
  const hash = location.hash.replace(/^#/, "");
  const [panel, query] = hash.split("?");
  const params = new URLSearchParams(query || "");
  for (const key of URL_KEYS) {
    if (params.has(key)) state[key] = params.get(key);
  }
  return panel || "";
}

function writeUrl() {
  const params = new URLSearchParams();
  for (const key of URL_KEYS) {
    if (state[key]) params.set(key, state[key]);
  }
  const query = params.toString();
  const next = `#${state.current || ""}${query ? "?" + query : ""}`;
  if (next !== location.hash) history.replaceState(null, "", next);
}

/* ------------------------------------------------------------------ helpers */

function node(tag, attrs = {}, children = []) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") element.textContent = value;
    else if (key === "html") element.innerHTML = value;
    else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else element.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children)) {
    if (child) element.append(child);
  }
  return element;
}

function lamp(label, stateName) {
  return node("span", { class: "lamp", "data-state": stateName, text: label });
}

function cookie(name) {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[2]) : "";
}

async function call(path, options = {}) {
  const headers = { [HEADER]: "1", Accept: "application/json" };
  if (options.body) {
    headers["Content-Type"] = "application/json";
    headers["X-CSRFToken"] = cookie("csrftoken");
  }
  const response = await fetch(API + path, {
    method: options.body ? "POST" : "GET",
    credentials: "same-origin",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  return {
    ok: response.ok,
    status: response.status,
    outcome: response.headers.get("X-Console-Outcome") || "",
    retryable: response.headers.get("X-Console-Retryable") === "true",
    payload: payload || {},
  };
}

/* The five outcomes are reported, not collapsed. An operator must be able to
 * tell "the operation did not start" from "the outcome is unknown", because
 * the second one forbids a retry. */
function report(result) {
  if (result.ok) {
    el.notice.hidden = true;
    return true;
  }
  const detail = result.payload.detail || `THE REQUEST FAILED. STATUS ${result.status}.`;
  const kind = result.outcome === "unavailable" ? "attn" : "fail";
  const legend =
    result.outcome === "indeterminate"
      ? "OUTCOME UNKNOWN. DO NOT REPEAT THE OPERATION."
      : result.retryable
        ? "THE OPERATION DID NOT START. YOU CAN REPEAT IT."
        : "THE OPERATION FAILED.";
  el.notice.textContent = "";
  el.notice.dataset.kind = kind;
  el.notice.append(node("strong", { class: "notice-legend", text: legend }));
  el.notice.append(document.createTextNode(detail));
  el.notice.hidden = false;
  return false;
}

/* --------------------------------------------------------------- top strip */

function drawStrip(root) {
  el.version.textContent = root.version || "";
  el.actor.textContent = root.actor ? `${root.actor.name}` : "";
  el.lamps.textContent = "";
  const database = lamp("DATABASE", "ok");
  database.dataset.check = "database";
  el.lamps.append(database);
  const server = lamp("GAME SERVER", root.degraded ? "attn" : "ok");
  server.dataset.check = "io_owner";
  el.lamps.append(server);
  const live = lamp("LIVE", "attn");
  live.dataset.live = "1";
  live.title = "The live feed";
  el.lamps.append(live);
  if (root.settings) {
    for (const [key, label] of [
      ["repl_enabled", "REPL"],
      ["sql_enabled", "SQL"],
      ["server_control_enabled", "SERVER CONTROL"],
    ]) {
      el.lamps.append(lamp(label, root.settings[key] ? "attn" : "off"));
    }
  }
}

/* -------------------------------------------------------------------- rail */

function drawRail() {
  el.rail.textContent = "";
  el.rail.append(
    node("div", { class: "rail-heading" }, [
      node("span", { class: "legend", text: "Stations" }),
      // Discoverability: a shortcut nobody is told about is a shortcut nobody
      // uses, and this one is how you reach a station without the rail.
      node("button", {
        class: "rail-palette",
        type: "button",
        title: "Go to a station",
        text: "CTRL K",
        onclick: () => openPalette(),
      }),
    ]),
  );
  for (const panel of state.panels) {
    el.rail.append(
      node("button", {
        class: "rail-item",
        type: "button",
        "aria-current": panel.key === state.current ? "true" : "false",
        onclick: () => select(panel.key),
      }, [
        document.createTextNode(panel.label),
        panel.description ? node("small", { text: panel.description }) : null,
      ]),
    );
  }
  if (state.degraded) {
    el.rail.append(
      node("p", {
        class: "rail-note",
        text: "THE GAME SERVER IS NOT AVAILABLE. THE PANELS THAT READ THE DATABASE CONTINUE TO OPERATE. THE PANELS THAT CHANGE GAME STATE ARE DISABLED.",
      }),
    );
  }
}

/* ------------------------------------------------------------------ panels */

function head(title, countText) {
  return node("div", { class: "panel-head" }, [
    node("h1", { class: "panel-title", text: title }),
    countText ? node("span", { class: "panel-count", text: countText }) : null,
  ]);
}

function empty(line, hint) {
  return node("div", { class: "empty" }, [
    node("p", { class: "empty-line", text: line }),
    hint ? node("p", { class: "empty-hint", text: hint }) : null,
  ]);
}

function cell(value) {
  if (value === null || value === undefined || value === "") {
    return node("td", { class: "null", text: "--" });
  }
  const numeric = typeof value === "number";
  return node("td", {
    class: numeric ? "num" : null,
    title: String(value),
    text: String(value),
  });
}

/* Show or hide the bulk control without redrawing the table, so a page of
 * checkboxes does not reset while the operator is still ticking them. */
function paintChosen() {
  const button = document.getElementById("bulk-delete");
  if (!button) return;
  button.hidden = state.chosen.length === 0;
  button.textContent = "DELETE " + state.chosen.length + " SELECTED";
}

/* An export leaves the console. The server records what was taken and by whom
 * before it answers, so the download and the record cannot disagree. */
async function runExport(format) {
  const query = new URLSearchParams({
    model: state.model || "",
    search: state.search || "",
    order: state.order || "",
    columns: state.columns || "",
  });
  const result = await call("panels/records/actions/export/?" + query, {
    body: {
      model: state.model,
      fmt: format,
      search: state.search || "",
      order: state.order || "",
      columns: state.columns || "",
    },
  });
  if (!report(result)) return;
  const data = result.payload.result || {};
  const blob = new Blob([data.body || ""], { type: data.content_type || "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = node("a", { href: url, download: data.filename || "export.txt" });
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/* Counted first, decided second. Django admin commits and reports afterwards;
 * this reports and then asks. */
async function previewDelete(ids) {
  const result = await call("panels/records/actions/preview_delete/", {
    body: { model: state.model, ids },
  });
  if (!report(result)) return;
  const data = result.payload.result || {};
  const lines = (data.rows || [])
    .slice(0, 12)
    .map((row) => "  #" + row.id + " removes " + row.reach + " related row(s)");
  const missing = (data.missing || []).length
    ? "\n" + data.missing.length + " selected row(s) no longer exist."
    : "";
  const proceed = confirm(
    "Delete " +
      data.found +
      " row(s) from " +
      data.model +
      "?\n\nThe database also removes " +
      data.total_cascade +
      " related row(s):\n" +
      lines.join("\n") +
      missing +
      "\n\nThis cannot be undone.",
  );
  if (!proceed) return;
  const reason = prompt("Why are these rows being deleted?");
  if (!reason) return;
  const done = await call("panels/records/actions/delete/", {
    body: { model: state.model, ids, reason },
  });
  if (report(done)) {
    state.chosen = [];
    select_render();
  }
}

async function drawRecords() {
  const models = await call("panels/records/actions/models/", { body: {} });
  if (!report(models)) {
    el.station.textContent = "";
    el.station.append(head("RECORDS"), empty("THE MODEL LIST IS NOT AVAILABLE."));
    return;
  }
  const list = models.payload.result || [];
  if (!state.model && list.length) state.model = list[0].label;

  const picker = node("select", {
    id: "model-select",
    onchange: (event) => {
      state.model = event.target.value;
      state.cursor = "";
      state.trail = [];
      state.order = "";
      state.columns = "";
      select_render();
    },
  });
  for (const item of list) {
    picker.append(
      node("option", {
        value: item.label,
        selected: item.label === state.model,
        text: `${item.label}  (${item.verbose_name_plural})`,
      }),
    );
  }

  const search = node("input", {
    type: "search",
    value: state.search,
    placeholder: "SEARCH BY PREFIX",
    "aria-label": "Search identifying columns by prefix",
    onchange: (event) => {
      state.search = event.target.value;
      state.cursor = "";
      state.trail = [];
      select_render();
    },
  });

  const query = new URLSearchParams({
    model: state.model,
    search: state.search,
    order: state.order,
    columns: state.columns,
    cursor: state.cursor,
  });
  const result = await call(`panels/records/rows/?${query}`);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (!result.ok) {
    report(result);
    body.append(empty("THE ROWS ARE NOT AVAILABLE.", result.payload.detail || ""));
  } else if (!data.rows || data.rows.length === 0) {
    body.append(
      empty(
        "NO ROWS.",
        state.search
          ? "No row starts with the search text. Search matches the start of a value, not the middle."
          : "This model has no rows.",
      ),
    );
  } else {
    const table = node("table");
    const headRow = node("tr");
    if (data.writable) {
      headRow.append(
        node("th", { scope: "col", class: "pick" }, [
          node("input", {
            type: "checkbox",
            "aria-label": "Select every row on this page",
            checked: state.chosen.length > 0 && state.chosen.length === data.rows.length,
            onchange: (event) => {
              state.chosen = event.target.checked
                ? data.rows.map((row) => String(row[data.columns[0]]))
                : [];
              select_render();
            },
          }),
        ]),
      );
    }
    for (const field of data.columns) {
      const next = state.order === field ? `-${field}` : field;
      headRow.append(
        node("th", { scope: "col" }, [
          node("button", {
            type: "button",
            text: field + (state.order === field ? " ↑" : state.order === `-${field}` ? " ↓" : ""),
            onclick: () => {
              state.order = next;
              state.cursor = "";
              state.trail = [];
              select_render();
            },
          }),
        ]),
      );
    }
    table.append(node("thead", {}, [headRow]));
    const tbody = node("tbody");
    for (const row of data.rows) {
      const id = String(row[data.columns[0]]);
      const cells = data.columns.map((field) => cell(row[field]));
      if (data.writable) {
        cells.unshift(
          node("td", { class: "pick" }, [
            node("input", {
              type: "checkbox",
              "aria-label": "Select row " + id,
              checked: state.chosen.includes(id),
              // The row itself opens the editor. Without this a click meant
              // to select a row for deletion opens it instead.
              onclick: (event) => event.stopPropagation(),
              onchange: (event) => {
                state.chosen = event.target.checked
                  ? [...new Set([...state.chosen, id])]
                  : state.chosen.filter((value) => value !== id);
                paintChosen();
              },
            }),
          ]),
        );
      }
      tbody.append(
        node("tr", {
          title: data.writable ? "Open this row" : "",
          onclick: () => {
            if (!data.writable) return;
            state.editing = id;
            select_render();
          },
        }, cells),
      );
    }
    table.append(tbody);
    body.append(table);
  }

  /* Paging is by cursor, so there is no page number to show and no total to
   * count for one. PREVIOUS walks back through the cursors already visited. */
  const first = node("button", {
    type: "button",
    text: "FIRST",
    disabled: state.trail.length === 0 && !state.cursor,
    onclick: () => { state.cursor = ""; state.trail = []; select_render(); },
  });
  const previous = node("button", {
    type: "button",
    text: "PREVIOUS",
    disabled: state.trail.length === 0,
    onclick: () => { state.cursor = state.trail.pop() || ""; select_render(); },
  });
  const next = node("button", {
    type: "button",
    text: "NEXT",
    disabled: !data.has_more,
    onclick: () => { state.trail.push(state.cursor); state.cursor = data.next_cursor; select_render(); },
  });

  const bulk = node("button", {
    type: "button",
    id: "bulk-delete",
    text: "DELETE SELECTED",
    hidden: state.chosen.length === 0,
    onclick: () => previewDelete(state.chosen),
  });

  const exportCsv = node("button", {
    type: "button",
    text: "EXPORT CSV",
    title: "Download these rows. The console records the export.",
    onclick: () => runExport("csv"),
  });
  const exportJson = node("button", {
    type: "button",
    text: "EXPORT JSON",
    title: "Download these rows. The console records the export.",
    onclick: () => runExport("json"),
  });

  const share = node("button", {
    type: "button",
    text: "COPY LINK",
    title: "Copy a link to this exact view",
    onclick: async (event) => {
      const button = event.target;
      try {
        await navigator.clipboard.writeText(location.href);
        button.textContent = "COPIED";
      } catch {
        button.textContent = "COPY FAILED";
      }
      setTimeout(() => { button.textContent = "COPY LINK"; }, 1500);
    },
  });

  const total = node("button", {
    type: "button",
    text: "COUNT ROWS",
    onclick: async (event) => {
      const button = event.target;
      button.disabled = true;
      button.textContent = "COUNTING";
      const counted = await call("panels/records/actions/count/", {
        body: { model: state.model, search: state.search },
      });
      const payload = counted.payload.result || {};
      button.textContent = payload.exact
        ? `${payload.rows} ROWS`
        : `ABOUT ${payload.rows} ROWS`;
      button.disabled = false;
      button.title = payload.reason || "";
    },
  });

  /* The editor is a region of the station, not a modal. Nothing here needs
   * protected focus, and an operator comparing a value against the row above
   * should not have the table hidden behind a sheet. */
  if (state.editing !== null) {
    body.prepend(await editor(data));
  }

  const add = node("button", {
    type: "button",
    text: "ADD ROW",
    disabled: !data.writable,
    title: data.writable ? "" : `Write it through ${data.write_via}`,
    onclick: () => { state.editing = "new"; select_render(); },
  });

  el.station.textContent = "";
  el.station.append(
    head("RECORDS", data.storage ? data.storage.toUpperCase() : ""),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "model-select", text: "Model" }),
        picker,
      ]),
      node("div", { class: "field" }, [search]),
      node("span", { class: "spacer" }),
      data.writable ? lamp("WRITE ENABLED", "ok") : lamp("DOMAIN OWNED", "attn"),
      add,
      bulk,
      share,
      saveViewButton(),
      exportCsv,
      exportJson,
      total,
      first,
      previous,
      next,
    ]),
    body,
  );

  if (!data.writable && data.write_via) {
    body.prepend(
      node("div", { class: "empty" }, [
        node("p", { class: "empty-line", text: "THIS MODEL IS READ ONLY." }),
        node("p", { class: "empty-hint", text: `Change it here: ${data.write_via}` }),
      ]),
    );
  }
}

/* A form built from what the service will actually accept, not from the
 * model's columns. A field the mutation adapter does not declare cannot be
 * written, and offering it would produce a rejection after the operator has
 * already filled it in. */
async function editor(data) {
  const shape = await call("panels/records/actions/form/", { body: { model: state.model } });
  const spec = shape.payload.result || {};
  const wrap = node("div", { class: "editor" });
  const creating = state.editing === "new";

  let current = {};
  if (!creating) {
    const detail = await call(
      `panels/records/detail/${encodeURIComponent(state.editing)}/?model=${encodeURIComponent(state.model)}`,
    );
    current = (detail.payload.record || {}).record || detail.payload.record || {};
  }

  const inputs = {};
  const grid = node("div", { class: "editor-grid" });
  for (const field of spec.fields || []) {
    const value = current[field.name];
    const input = node("input", {
      type: "text",
      id: `edit-${field.name}`,
      value: value === null || value === undefined ? "" : String(value),
      placeholder: field.types.join(" or "),
    });
    inputs[field.name] = input;
    grid.append(
      node("div", { class: "editor-field" }, [
        node("label", { class: "legend", for: `edit-${field.name}`, text: field.name }),
        input,
      ]),
    );
  }

  const status = node("p", { class: "empty-hint" });

  const save = node("button", {
    type: "button",
    text: creating ? "CREATE ROW" : "SAVE CHANGES",
    onclick: async () => {
      const values = {};
      for (const [name, input] of Object.entries(inputs)) {
        values[name] = input.value;
      }
      const result = await call("panels/records/actions/save/", {
        body: { model: state.model, pk: creating ? null : state.editing, values },
      });
      if (!result.ok) {
        report(result);
        const detail = result.payload.detail || "THE ROW WAS NOT SAVED.";
        status.textContent = String(detail);
        const field = result.payload.field;
        if (field && inputs[field]) inputs[field].focus();
        return;
      }
      state.editing = null;
      state.cursor = "";
      state.trail = [];
      select_render();
    },
  });

  const cancel = node("button", {
    type: "button",
    text: "CANCEL",
    onclick: () => { state.editing = null; select_render(); },
  });

  /* A duration is required. The old banlist had no expiry column, so every
   * entry in it was permanent by default; that is the mistake this field
   * exists to stop repeating. */
  const duration = node("input", {
    type: "text",
    value: "7d",
    size: "6",
    "aria-label": "How long the sanction lasts",
    title: "30m, 12h, 7d, 2w, or perm",
    placeholder: "7d",
  });
  const reach = node("div", { class: "collateral" });

  wrap.append(
    node("div", { class: "editor-head" }, [
      node("span", { class: "legend", text: creating ? "NEW ROW" : `ROW ${state.editing}` }),
      node("span", { class: "spacer" }),
      save,
      cancel,
    ]),
    grid,
    status,
  );
  if (spec.note) wrap.append(node("p", { class: "empty-hint", text: spec.note }));
  return wrap;
}

async function drawMigrations() {
  const result = await call("panels/migrations/rows/");
  report(result);
  const data = result.payload.rows || {};
  const rows = data.rows || [];
  const body = node("div", { class: "panel-body" });

  if (rows.length === 0) {
    body.append(empty("NO MIGRATIONS."));
  } else {
    const table = node("table");
    table.append(
      node("thead", {}, [
        node("tr", {}, [
          node("th", { scope: "col", text: "STATE" }),
          node("th", { scope: "col", text: "APP" }),
          node("th", { scope: "col", text: "NAME" }),
        ]),
      ]),
    );
    const tbody = node("tbody");
    for (const row of rows) {
      tbody.append(
        node("tr", {}, [
          node("td", {}, [lamp(row.applied ? "APPLIED" : "PENDING", row.applied ? "ok" : "attn")]),
          cell(row.app),
          cell(row.name),
        ]),
      );
    }
    table.append(tbody);
    body.append(table);
  }

  const pending = data.pending_count || 0;
  el.station.textContent = "";
  el.station.append(
    head("MIGRATIONS", `${rows.length} TOTAL`),
    node("div", { class: "toolbar" }, [
      lamp(pending ? `${pending} PENDING` : "NONE PENDING", pending ? "attn" : "ok"),
      node("span", { class: "legend", text: pending ? "MIGRATE RUNS THESE ON THE NEXT RELOAD." : "MIGRATE HAS NOTHING TO APPLY." }),
    ]),
    body,
  );
}

async function drawSettings() {
  const result = await call("panels/settings/rows/");
  report(result);
  const data = result.payload.rows || {};
  const rows = data.rows || [];
  const body = node("div", { class: "panel-body" });
  const list = node("dl", { class: "rows" });

  for (const row of rows) {
    list.append(
      node("div", { class: "row-pair" }, [
        node("dt", { text: row.name }),
        node("dd", {
          class: row.overridden ? "overridden" : null,
          text: typeof row.value === "string" ? row.value : JSON.stringify(row.value),
        }),
      ]),
    );
  }
  body.append(rows.length ? list : empty("NO SETTINGS."));

  el.station.textContent = "";
  el.station.append(
    head("SETTINGS", `${data.total || 0} TOTAL`),
    node("div", { class: "toolbar" }, [
      lamp(`${data.overridden || 0} CHANGED BY THE GAME`, "attn"),
      node("span", { class: "legend", text: "A CHANGED VALUE IS SHOWN IN AMBER. A SECRET VALUE STAYS ON THE SERVER." }),
    ]),
    body,
  );
}

async function drawHealth() {
  const result = await call("panels/health/rows/");
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });
  const list = node("dl", { class: "rows" });

  for (const row of data.rows || []) {
    list.append(
      node("div", { class: "row-pair" }, [
        node("dt", { text: row.check.replace(/_/g, " ") }),
        node("dd", {}, [lamp(row.ok ? "OK" : "FAILED", row.ok ? "ok" : "fail")]),
      ]),
    );
  }
  list.append(
    node("div", { class: "row-pair" }, [
      node("dt", { text: "version" }),
      node("dd", { text: data.version || "--" }),
    ]),
  );
  body.append(list);

  el.station.textContent = "";
  el.station.append(
    head("HEALTH"),
    node("div", { class: "toolbar" }, [
      lamp(data.healthy ? "SERVER IS WELL" : "SERVER NEEDS ATTENTION", data.healthy ? "ok" : "fail"),
    ]),
    body,
  );
}

/* Attributes inverts the usual lens. Keys are unbounded in this engine -- any
 * object may carry any key -- so a table of objects never helps you find the
 * one key you care about. You browse keys, then reach objects through them.
 * The catalogue is a sample and says so; exact counts come from the index on
 * request, one key at a time. */
async function drawAttributes() {
  const model = state.attrModel || "";
  const query = new URLSearchParams({ model, search: state.attrSearch || "" });
  const result = await call(`panels/attributes/rows/?${query}`);
  report(result);
  const data = result.payload.rows || {};
  const rows = data.rows || [];
  if (!state.attrModel && data.model) state.attrModel = data.model;

  const picker = node("select", {
    id: "attr-model",
    onchange: (event) => {
      state.attrModel = event.target.value;
      select_render();
    },
  });
  for (const label of data.models || []) {
    picker.append(node("option", { value: label, selected: label === data.model, text: label }));
  }

  const search = node("input", {
    type: "search",
    value: state.attrSearch || "",
    placeholder: "SEARCH KEYS",
    "aria-label": "Search attribute keys",
    onchange: (event) => {
      state.attrSearch = event.target.value;
      select_render();
    },
  });

  const body = node("div", { class: "panel-body" });
  if (rows.length === 0) {
    body.append(
      empty(
        "NO ATTRIBUTE KEYS.",
        state.attrSearch
          ? "No key matches the search text. Clear the search to show all keys."
          : "The objects that were read carry no attributes.",
      ),
    );
  } else {
    const table = node("table");
    table.append(
      node("thead", {}, [
        node("tr", {}, [
          node("th", { scope: "col", text: "KEY" }),
          node("th", { scope: "col", text: "CATEGORY" }),
          node("th", { scope: "col", text: "OBJECTS" }),
          node("th", { scope: "col", text: "TYPE" }),
          node("th", { scope: "col", text: "EXAMPLE" }),
        ]),
      ]),
    );
    const tbody = node("tbody");
    for (const row of rows) {
      tbody.append(
        node("tr", {}, [
          node("td", {}, [
            node("button", {
              type: "button",
              class: "linky",
              text: row.key,
              title: "Show the objects that hold this key.",
              onclick: () => {
                state.attrKeyOpen = row.key;
                state.attrObject = "";
                select_render();
              },
            }),
          ]),
          node("td", { class: row.category ? null : "null", text: row.category || "(default)" }),
          node("td", { class: "num", text: String(row.objects) }),
          cell(row.kinds.join(", ")),
          cell(row.example),
        ]),
      );
    }
    table.append(tbody);
    body.append(table);
  }

  if (state.attrObject) {
    body.prepend(await attrDocument(data.model));
  } else if (state.attrKeyOpen) {
    body.prepend(await attrCarriers(data.model, state.attrKeyOpen));
  }

  const sample = data.sample || {};
  el.station.textContent = "";
  el.station.append(
    head("ATTRIBUTES", data.key_count ? `${data.key_count} KEYS` : ""),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "attr-model", text: "Model" }),
        picker,
      ]),
      node("div", { class: "field" }, [search]),
      node("span", { class: "spacer" }),
      lamp(sample.complete ? "WHOLE TABLE" : `SAMPLE OF ${sample.documents_read || 0}`,
           sample.complete ? "ok" : "attn"),
    ]),
    body,
  );

  if (!sample.complete && sample.note) {
    body.prepend(
      node("div", { class: "empty" }, [
        node("p", { class: "empty-line", text: "THESE COUNTS ARE NOT EXACT." }),
        node("p", { class: "empty-hint", text: sample.note }),
      ]),
    );
  }
}

/* The objects carrying one key. This is the step between "which keys exist"
 * and "what does this object hold": without it an operator has a catalogue and
 * no way to reach a single document from it. */
async function attrCarriers(model, key) {
  const wrap = node("div", { class: "detail" });
  const result = await call("panels/attributes/actions/carriers/", {
    body: { model: model || "", key },
  });
  wrap.append(
    node("div", { class: "toolbar" }, [
      node("span", { class: "legend", text: "OBJECTS CARRYING " + key.toUpperCase() }),
      node("span", { class: "spacer" }),
      node("button", {
        type: "button",
        text: "CLOSE",
        onclick: () => {
          state.attrKeyOpen = "";
          select_render();
        },
      }),
    ]),
  );
  if (!report(result)) return wrap;
  const data = result.payload.result || {};
  const rows = data.rows || [];
  if (!rows.length) {
    wrap.append(empty("NO OBJECT CARRIES THIS KEY."));
    return wrap;
  }
  wrap.append(
    dataTable(
      ["ID", "NAME", "VALUE", ""],
      rows.map((row) => [
        node("td", { class: "num", text: String(row.id) }),
        cell(row.name || ""),
        cell(row.value || ""),
        node("td", {}, [
          node("button", {
            type: "button",
            text: "DOCUMENT",
            onclick: () => {
              state.attrObject = String(row.id);
              select_render();
            },
          }),
        ]),
      ]),
    ),
  );
  if (data.note) wrap.append(node("p", { class: "empty-hint", text: data.note }));
  return wrap;
}

/* One value, walked rather than truncated. A dict of dicts flattened into a
 * 400-character preview is not readable and not navigable; this opens. */
function attrTree(name, treeNode, depth) {
  const label = node("span", { class: "tree-name", text: name });
  const kind = node("span", { class: "tree-kind", text: treeNode.kind });
  const summary = node("span", { class: "tree-summary", text: treeNode.summary || "" });
  const line = node("div", { class: "tree-line", style: "padding-left:" + depth * 14 + "px" });

  if (!(treeNode.children || []).length) {
    line.append(label, kind, summary);
    return [line];
  }

  const children = node("div", { hidden: depth > 0 });
  const toggle = node("button", {
    type: "button",
    class: "tree-toggle",
    "aria-expanded": depth === 0 ? "true" : "false",
    text: depth === 0 ? "-" : "+",
    onclick: () => {
      const open = children.hidden;
      children.hidden = !open;
      toggle.textContent = open ? "-" : "+";
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    },
  });
  line.append(toggle, label, kind, summary);

  for (const child of treeNode.children) {
    for (const element of attrTree(child.name, child, depth + 1)) children.append(element);
  }
  if (treeNode.truncated) {
    children.append(
      node("div", {
        class: "tree-line empty-hint",
        style: "padding-left:" + (depth + 1) * 14 + "px",
        text: "This value contains " + treeNode.truncated + " more entries. This page does not show them.",
      }),
    );
  }
  return [line, children];
}

async function attrDocument(model) {
  const wrap = node("div", { class: "detail" });
  const result = await call(
    "panels/attributes/detail/" + encodeURIComponent(state.attrObject) +
      "/?model=" + encodeURIComponent(model || ""),
  );
  wrap.append(
    node("div", { class: "toolbar" }, [
      node("span", { class: "legend", text: "ATTRIBUTE DOCUMENT" }),
      node("span", { class: "spacer" }),
      node("button", {
        type: "button",
        text: "CLOSE",
        onclick: () => {
          state.attrObject = "";
          select_render();
        },
      }),
    ]),
  );
  if (!report(result)) return wrap;
  const data = result.payload.record || {};

  wrap.append(
    node("dl", { class: "rows" }, [
      node("div", { class: "row-pair" }, [
        node("dt", { text: "object" }),
        node("dd", { text: "#" + data.id + " " + (data.name || "") }),
      ]),
      node("div", { class: "row-pair" }, [
        node("dt", { text: "entries" }),
        node("dd", { class: "num", text: String(data.entry_count ?? 0) }),
      ]),
      node("div", { class: "row-pair" }, [
        node("dt", { text: "document size" }),
        node("dd", {}, [
          lamp(data.size_bytes + " BYTES", data.fat ? "attn" : "ok"),
          data.fat ? node("span", { class: "empty-hint", text: " " + data.size_note }) : null,
        ]),
      ]),
    ]),
  );

  for (const group of data.categories || []) {
    wrap.append(section(group.category ? "Category: " + group.category : "Default category"));
    const tree = node("div", { class: "tree" });
    for (const entry of group.entries || []) {
      const head = node("div", { class: "tree-entry" }, [
        node("span", { class: "tree-key", text: entry.key }),
        node("button", {
          type: "button",
          text: "EDIT",
          disabled: !entry.editable,
          title: entry.editable
            ? "Change this value."
            : "You cannot edit a packed Python object as JSON.",
          onclick: () => attrEdit(data.model, data.id, entry.key, group.category, entry.value),
        }),
        node("button", {
          type: "button",
          text: "REMOVE",
          onclick: () => attrRemove(data.model, data.id, entry.key, group.category),
        }),
      ]);
      tree.append(head);
      for (const element of attrTree(entry.key, entry.tree || {}, 0)) tree.append(element);
    }
    wrap.append(tree);
  }

  wrap.append(
    node("div", { class: "fault-actions" }, [
      node("button", {
        type: "button",
        text: "ADD AN ATTRIBUTE",
        onclick: () => {
          const key = prompt("Attribute key");
          if (!key) return;
          attrEdit(data.model, data.id, key, "", "");
        },
      }),
    ]),
  );
  return wrap;
}

/* JSON, not a guess. An operator who types 123 means the number and one who
 * types "123" means the string, and a panel that decides for them stores the
 * wrong type into a document nothing else validates. */
async function attrEdit(model, pk, key, category, current) {
  const value = prompt(
    "Enter the value for " + key + " as JSON.\n" +
      "Put quotation marks around text. Write a number without quotation marks.",
    current || "",
  );
  if (value === null) return;
  const reason = prompt("Why is this attribute being changed?");
  if (!reason) return;
  const done = await call("panels/attributes/actions/set/", {
    body: { model, pk, key, category: category || "", value, reason },
  });
  if (report(done)) select_render();
}

async function attrRemove(model, pk, key, category) {
  const reason = prompt('Why is "' + key + '" being removed?');
  if (!reason) return;
  const done = await call("panels/attributes/actions/unset/", {
    body: { model, pk, key, category: category || "", reason },
  });
  if (report(done)) select_render();
}

async function drawRuntime() {
  const result = await call("panels/runtime/rows/");
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  const alarms = node("dl", { class: "rows" });
  for (const alarm of data.alarms || []) {
    alarms.append(
      node("div", { class: "row-pair" }, [
        node("dt", { text: alarm.name.replace(/^evennia_/, "").replace(/_/g, " ") }),
        node("dd", {}, [
          lamp(alarm.ok ? "ZERO" : String(alarm.value), alarm.ok ? "ok" : "fail"),
          node("span", { class: "empty-hint", text: " " + alarm.meaning }),
        ]),
      ]),
    );
  }

  const caches = node("dl", { class: "rows" });
  for (const cache of data.caches || []) {
    caches.append(
      node("div", { class: "row-pair" }, [
        node("dt", { text: cache.name }),
        node("dd", {}, [
          node("span", {
            text:
              cache.hit_rate === null
                ? "not used yet"
                : (cache.hit_rate * 100).toFixed(1) + "% hit",
          }),
          node("span", { class: "empty-hint", text: " " + cache.invalidation }),
        ]),
      ]),
    );
  }

  body.append(section("Alarms"), alarms, section("Caches"), caches);

  /* The scheduler, which is "@systems as a page". A system that skips is one
   * whose run outlasts its own cadence, and no other number on this station
   * shows that. */
  body.append(section("Scheduled systems"));
  const systems = data.systems || {};
  if (!systems.available) {
    body.append(empty("THE SCHEDULER CANNOT BE READ.", systems.reason || ""));
  } else if (!(systems.rows || []).length) {
    body.append(empty("NO SYSTEM IS REGISTERED.", systems.reason || ""));
  } else {
    body.append(
      dataTable(
        ["SYSTEM", "CADENCE", "SCOPE", "WORKLOAD", "FIRES", "SKIPS", "LAST RUN"],
        systems.rows.map((row) => [
          cell(row.name),
          cell(row.cadence),
          cell(row.scope),
          cell(row.workload),
          node("td", { class: "num", text: String(row.fires) }),
          node("td", {
            class: row.skips ? "num fail-text" : "num",
            text: String(row.skips),
          }),
          node("td", {}, [
            row.in_flight ? lamp("RUNNING", "attn") : null,
            node("span", { text: row.last_run || "--" }),
          ]),
        ]),
      ),
    );
  }

  /* Task roots. The unmanaged-access counter is an alarm and is already above;
   * these three are the supervision picture behind it. */
  const tasks = data.tasks || {};
  const taskRows = node("dl", { class: "rows" });
  for (const [label, key] of [
    ["active task roots", "active"],
    ["task roots started", "started"],
    ["database scope closes", "db_scope_closes"],
  ]) {
    taskRows.append(
      node("div", { class: "row-pair" }, [
        node("dt", { text: label }),
        node("dd", { class: "num", text: String(tasks[key] ?? 0) }),
      ]),
    );
  }
  body.append(section("Supervised tasks"), taskRows);

  body.append(section("Metrics"));
  body.append(node("div", { id: "live-metrics" }));

  el.station.textContent = "";
  el.station.append(
    head("RUNTIME"),
    node("div", { class: "toolbar" }, [
      lamp(
        data.metrics_available ? "METRICS LIVE" : "NO METRICS",
        data.metrics_available ? "ok" : "attn",
      ),
      data.reason ? node("span", { class: "legend", text: data.reason }) : null,
    ]),
    body,
  );
  paintMetrics();
}

function section(label) {
  return node("p", { class: "section-legend", text: label });
}

async function drawLogs() {
  const query = new URLSearchParams({
    file: state.logFile || "",
    search: state.logSearch || "",
    lines: "200",
  });
  const result = await call("panels/logs/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};

  const picker = node("select", {
    id: "log-file",
    onchange: (event) => {
      state.logFile = event.target.value;
      select_render();
    },
  });
  for (const file of data.files || []) {
    picker.append(
      node("option", {
        value: file.label,
        selected: file.label === data.file,
        text: file.label + " (" + file.size_bytes + " bytes)",
      }),
    );
  }

  const search = node("input", {
    type: "search",
    value: state.logSearch || "",
    placeholder: "FILTER BY TEXT OR PATTERN",
    "aria-label": "Filter log lines",
    onchange: (event) => {
      state.logSearch = event.target.value;
      select_render();
    },
  });

  const body = node("div", { class: "panel-body" });
  const history = node("div", { class: "log-view" });
  for (const row of data.rows || []) {
    history.append(
      node("div", { class: "log-line" }, [
        node("span", { class: "log-source", text: row.source }),
        node("span", { class: "log-text", text: row.line }),
      ]),
    );
  }
  body.append(section("Recorded"), history, section("Live"));
  body.append(node("div", { id: "live-log", class: "log-view" }));

  el.station.textContent = "";
  el.station.append(
    head("LOGS", data.line_count ? data.line_count + " LINES" : ""),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "log-file", text: "File" }),
        picker,
      ]),
      node("div", { class: "field" }, [search]),
      node("span", { class: "spacer" }),
      lamp((data.backups || []).length + " BACKUPS", "off"),
    ]),
    body,
  );
  appendLiveLog();
}

async function drawErrors() {
  const query = new URLSearchParams({
    state: state.errorState || "",
    search: state.errorSearch || "",
  });
  const result = await call("panels/errors/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const rows = data.rows || [];

  const filter = node("select", {
    id: "error-state",
    onchange: (event) => {
      state.errorState = event.target.value;
      select_render();
    },
  });
  for (const option of [""].concat(data.states || [])) {
    filter.append(
      node("option", {
        value: option,
        selected: option === (state.errorState || ""),
        text: option ? option.toUpperCase() : "ALL STATES",
      }),
    );
  }

  const search = node("input", {
    type: "search",
    value: state.errorSearch || "",
    placeholder: "SEARCH EXCEPTION OR MESSAGE",
    "aria-label": "Search faults",
    onchange: (event) => {
      state.errorSearch = event.target.value;
      select_render();
    },
  });

  const body = node("div", { class: "panel-body" });

  /* A fault nobody has judged is the one that wants a person. A muted or
   * acknowledged one has already had the decision it needs. */
  const unjudged = rows.filter((group) => group.state === "open");
  const loud = unjudged.filter((group) => (group.count || 0) >= 100);
  body.append(
    annunciator(
      [
        loud.length && { text: loud.length + " FAULTS OVER 100 OCCURRENCES", state: "fail" },
        unjudged.length && { text: unjudged.length + " FAULTS NOBODY HAS JUDGED", state: "attn" },
      ],
      "EVERY FAULT HAS BEEN JUDGED",
    ),
  );

  if (rows.length === 0) {
    body.append(
      empty(
        "NO FAULTS.",
        state.errorSearch || state.errorState
          ? "No fault matches the filter. Clear it to show all faults."
          : "No traceback appears in the recent end of the log files.",
      ),
    );
  } else {
    for (const group of rows) {
      body.append(faultRow(group));
    }
  }

  el.station.textContent = "";
  el.station.append(
    head("ERRORS", data.group_count ? data.group_count + " FAULTS" : ""),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "error-state", text: "State" }),
        filter,
      ]),
      node("div", { class: "field" }, [search]),
      node("span", { class: "spacer" }),
      lamp(data.occurrence_count + " OCCURRENCES", "off"),
    ]),
    body,
  );
}

/* One fault, collapsed. The count is the point: an operator needs to know a
 * thing is happening constantly before they need its stack. */
function faultRow(group) {
  const open = state.errorOpen === group.signature;
  const wrap = node("div", { class: "fault" });

  const stateLamp =
    group.state === "muted"
      ? lamp("MUTED", "off")
      : group.state === "acknowledged"
        ? lamp("SEEN", "attn")
        : lamp("OPEN", "fail");

  wrap.append(
    node("div", {
      class: "fault-head",
      onclick: () => {
        state.errorOpen = open ? null : group.signature;
        select_render();
      },
    }, [
      stateLamp,
      node("span", { class: "fault-name", text: group.exception }),
      node("span", { class: "fault-message", text: group.message }),
      node("span", { class: "fault-count", text: "x" + group.count }),
      node("span", { class: "fault-when", text: group.last_seen }),
    ]),
  );

  if (!open) return wrap;

  const frames = node("div", { class: "fault-frames" });
  for (const frame of group.frames || []) {
    frames.append(
      node("div", { class: "log-line" }, [
        node("span", { class: "log-source", text: String(frame.line) }),
        node("span", { class: "log-text", text: frame.function + "  " + frame.file }),
      ]),
    );
  }

  const note = node("input", {
    type: "text",
    placeholder: "WHY, FOR WHOEVER READS THIS NEXT",
    "aria-label": "Review note",
    value: group.note || "",
  });

  const actions = node("div", { class: "fault-actions" }, [
    note,
    node("button", {
      type: "button",
      text: "ACKNOWLEDGE",
      onclick: () => reviewFault(group.signature, "acknowledged", note.value),
    }),
    node("button", {
      type: "button",
      text: "MUTE",
      title: "Hide until this fault's signature changes",
      onclick: () => reviewFault(group.signature, "muted", note.value),
    }),
    node("button", {
      type: "button",
      text: "REOPEN",
      onclick: () => reviewFault(group.signature, "open", note.value),
    }),
  ]);

  wrap.append(frames, actions);
  if (group.reviewed_by) {
    wrap.append(
      node("p", { class: "empty-hint", text: "Last reviewed by " + group.reviewed_by }),
    );
  }
  return wrap;
}

async function reviewFault(signature, wanted, note) {
  const result = await call("panels/errors/actions/review/", {
    body: { signature, state: wanted, note },
  });
  if (!report(result)) return;
  select_render();
}

/* The flag queue leads, because it is the only list here that is asking for
 * somebody's attention. Sanctions and sessions are reference material for
 * deciding what to do about a flag. */
/* The annunciator.
 *
 * A row that answers "is anything wrong" before the panel answers "what". It
 * came from the staff ticket board and belongs on every station that opens
 * with a queue: a table alone asks the operator to find what matters by
 * scanning it, which is the work a console exists to save.
 *
 * Two rules it carries with it. An alarm names a count and a condition --
 * "3 dead letters" -- never a severity word on its own, because "attention
 * needed" tells somebody to go and find out. And a panel with nothing wrong
 * still lights one block: an empty region reads as a panel that failed to
 * load, which is the one thing an operations surface must never look like.
 */
function annunciator(alarms, calm) {
  const row = node("div", { class: "annunciator" });
  const lit = (alarms || []).filter(Boolean);
  if (!lit.length) {
    row.append(lamp(calm || "NOTHING NEEDS A PERSON", "ok"));
    return row;
  }
  for (const alarm of lit) row.append(lamp(alarm.text, alarm.state));
  return row;
}

/* Count blocks that are their own filter.
 *
 * Readout and selector in one object. A row of chips beside a row of counts is
 * the same information twice, and the operator has to match them by eye to use
 * either.
 */
function countBlocks(items, current, onSelect) {
  const list = node("ul", { class: "kinds" });
  for (const item of items || []) {
    const chosen = String(item.key) === String(current || "");
    list.append(
      node("li", {}, [
        node(
          "button",
          {
            type: "button",
            class: "kind",
            "aria-current": chosen ? "true" : "false",
            onclick: () => onSelect(chosen ? "" : item.key),
          },
          [
            node("span", { class: "kind-name", text: item.label }),
            node("span", { class: "kind-count", text: String(item.count) }),
            node("span", {
              class: item.wants ? "kind-sub wants" : "kind-sub clear",
              text: item.sub || (item.wants ? item.wants + " need a person" : "clear"),
            }),
          ],
        ),
      ]),
    );
  }
  return list;
}

async function drawModeration() {
  const query = new URLSearchParams({ state: state.modState || "" });
  const result = await call("panels/moderation/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  const flags = data.flags || [];
  const severe = flags.filter((row) => (row.severity || 0) >= 2);
  body.append(
    annunciator(
      [
        severe.length && { text: severe.length + " HIGH-SEVERITY FLAGS", state: "fail" },
        flags.length && { text: flags.length + " FLAGS AWAITING A PERSON", state: "attn" },
      ],
      "NO FLAG IS WAITING",
    ),
  );

  body.append(section("Flags awaiting a person"));
  if ((data.flags || []).length === 0) {
    body.append(empty("QUEUE CLEAR.", "No flag is waiting for review."));
  } else {
    const table = node("table");
    table.append(
      node("thead", {}, [
        node("tr", {}, [
          node("th", { scope: "col", text: "SEV" }),
          node("th", { scope: "col", text: "KIND" }),
          node("th", { scope: "col", text: "ACCOUNT" }),
          node("th", { scope: "col", text: "SUMMARY" }),
          node("th", { scope: "col", text: "SEEN" }),
          node("th", { scope: "col", text: "LAST" }),
        ]),
      ]),
    );
    const tbody = node("tbody");
    for (const flag of data.flags) {
      tbody.append(
        node("tr", {
          title: "Open this flag",
          onclick: () => {
            state.modFlag = state.modFlag === flag.id ? null : flag.id;
            select_render();
          },
        }, [
          node("td", { class: "num", text: String(flag.severity) }),
          cell(flag.kind),
          cell(flag.account),
          cell(flag.summary),
          node("td", { class: "num", text: String(flag.seen_count) }),
          cell(flag.last_seen),
        ]),
      );
    }
    table.append(tbody);
    body.append(table);
  }

  if (state.modFlag !== null) {
    body.append(await flagDossier(data));
  }

  body.append(section("Active sanctions"));
  const sanctions = node("table");
  sanctions.append(
    node("thead", {}, [
      node("tr", {}, [
        node("th", { scope: "col", text: "LEVEL" }),
        node("th", { scope: "col", text: "SUBJECT" }),
        node("th", { scope: "col", text: "REASON" }),
        node("th", { scope: "col", text: "EXPIRES" }),
        node("th", { scope: "col", text: "" }),
      ]),
    ]),
  );
  const sbody = node("tbody");
  for (const item of data.sanctions || []) {
    sbody.append(
      node("tr", {}, [
        node("td", {}, [lamp(item.level.toUpperCase(), item.level === "watch" ? "off" : "fail")]),
        cell(item.subject_type + " " + item.subject),
        cell(item.reason),
        cell(item.expires),
        node("td", {}, [
          node("button", {
            type: "button",
            text: "LIFT",
            onclick: async () => {
              const reason = prompt("Why is this sanction being lifted?");
              if (!reason) return;
              const done = await call("panels/moderation/actions/revoke/", {
                body: { sanction_id: item.id, reason },
              });
              if (report(done)) select_render();
            },
          }),
        ]),
      ]),
    );
  }
  sanctions.append(sbody);
  body.append((data.sanctions || []).length ? sanctions : empty("NO ACTIVE SANCTIONS."));
  body.append(await drawProposals());

  body.append(section("Recent connections"));
  const sessions = node("div", { class: "log-view" });
  for (const row of data.sessions || []) {
    sessions.append(
      node("div", { class: "session-row" }, [
        node("span", { class: "log-source", text: row.protocol }),
        node("span", { class: "log-text", text: (row.account || "(anonymous)") + "  " + row.cidr + "  " + (row.network || "") }),
        row.address_trustworthy
          ? node("span", { class: "legend", text: row.country || "" })
          : lamp("ADDRESS VOID", "attn"),
      ]),
    );
  }
  body.append((data.sessions || []).length ? sessions : empty("NO CONNECTIONS RECORDED."));

  const filter = node("select", {
    id: "mod-state",
    onchange: (event) => {
      state.modState = event.target.value;
      state.modFlag = null;
      select_render();
    },
  });
  for (const option of [""].concat(data.flag_states || [])) {
    filter.append(
      node("option", {
        value: option,
        selected: option === (state.modState || ""),
        text: option ? option.toUpperCase() : "OPEN ONLY",
      }),
    );
  }

  el.station.textContent = "";
  el.station.append(
    head("MODERATION", data.open_flags ? data.open_flags + " OPEN" : "QUEUE CLEAR"),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "mod-state", text: "Show" }),
        filter,
      ]),
      node("span", { class: "spacer" }),
      node("span", { class: "legend", text: data.note || "" }),
      node("button", {
        type: "button",
        text: "VERIFY CHAIN",
        title: "Check the sanction hash chain",
        onclick: async (event) => {
          const button = event.target;
          button.textContent = "CHECKING";
          const done = await call("panels/moderation/actions/verify_chain/", { body: {} });
          const payload = done.payload.result || {};
          button.textContent = payload.ok === false ? "CHAIN BROKEN" : "CHAIN INTACT";
        },
      }),
    ]),
    body,
  );
}

async function flagDossier(queue) {
  const result = await call(
    "panels/moderation/detail/" + encodeURIComponent(state.modFlag) + "/",
  );
  if (!report(result)) return node("div");
  const flag = result.payload.record || {};
  const wrap = node("div", { class: "editor" });

  const evidence = node("dl", { class: "rows" });
  for (const row of flag.evidence || []) {
    evidence.append(
      node("div", { class: "row-pair" }, [
        node("dt", { text: row.key }),
        node("dd", { text: String(row.value) }),
      ]),
    );
  }

  const note = node("input", {
    type: "text",
    placeholder: "WHY, FOR WHOEVER READS THIS NEXT",
    "aria-label": "Resolution note",
  });

  const subjectType = node("select", { "aria-label": "Sanction subject type" });
  for (const option of queue.subject_types || []) {
    subjectType.append(node("option", { value: option, text: option }));
  }
  const subjectValue = node("input", {
    type: "text",
    placeholder: "SUBJECT VALUE",
    "aria-label": "Sanction subject value",
    value: flag.account || "",
  });
  const level = node("select", { "aria-label": "Sanction level" });
  for (const option of queue.levels || []) {
    level.append(node("option", { value: option, text: option }));
  }

  async function resolve(wanted) {
    const done = await call("panels/moderation/actions/resolve/", {
      body: { flag_id: flag.id, state: wanted, note: note.value },
    });
    if (report(done)) {
      state.modFlag = null;
      select_render();
    }
  }

  wrap.append(
    node("div", { class: "editor-head" }, [
      node("span", { class: "legend", text: "FLAG " + flag.id + "  " + flag.kind }),
      node("span", { class: "spacer" }),
      node("button", { type: "button", text: "DISMISS", onclick: () => resolve("dismissed") }),
      node("button", { type: "button", text: "ACKNOWLEDGE", onclick: () => resolve("acknowledged") }),
      node("button", {
        type: "button",
        text: "CLOSE",
        onclick: () => {
          state.modFlag = null;
          select_render();
        },
      }),
    ]),
    node("p", { class: "empty-hint", text: flag.summary || "" }),
    evidence,
    node("div", { class: "fault-actions" }, [note]),
    node("p", { class: "section-legend", text: "Issue a sanction from this flag" }),
    node("div", { class: "fault-actions" }, [
      subjectType,
      subjectValue,
      level,
      duration,
      node("button", {
        type: "button",
        text: "CHECK WHO THIS REACHES",
        title: "Count the accounts that have connected from this subject.",
        onclick: () => showCollateral(subjectType.value, subjectValue.value, reach),
      }),
      node("button", {
        type: "button",
        text: "SANCTION",
        onclick: async () => {
          const done = await call("panels/moderation/actions/sanction/", {
            body: {
              subject_type: subjectType.value,
              subject_value: subjectValue.value,
              level: level.value,
              reason: note.value,
              expires_at: duration.value,
              flag_id: flag.id,
            },
          });
          if (!report(done)) return;
          const result = done.payload.result || {};
          if (result.proposed) {
            // Not a failure. The console recorded a proposal instead, and the
            // operator has to be told that plainly or they will assume the ban
            // is in place.
            alert(result.message);
          }
          state.modFlag = null;
          select_render();
        },
      }),
    ]),
    reach,
  );
  return wrap;
}

/* What a ban on this subject would reach, before it is issued. A /24 can be one
 * household or a whole campus, and the two look identical in a form field. */
async function showCollateral(subjectType, subjectValue, target) {
  target.textContent = "";
  if (!subjectValue) return;
  const done = await call("panels/moderation/actions/collateral/", {
    body: { subject_type: subjectType, subject_value: subjectValue },
  });
  if (!report(done)) return;
  const data = done.payload.result || {};
  target.append(
    node("p", {}, [
      lamp(
        data.account_count + " ACCOUNT(S)",
        data.account_count > 1 ? "attn" : "ok",
      ),
      node("span", {
        class: "legend",
        text:
          " have connected from this subject, over " + data.sessions + " connection(s).",
      }),
    ]),
  );
  if ((data.accounts || []).length) {
    target.append(node("p", { class: "empty-hint", text: data.accounts.join(", ") }));
  }
  target.append(node("p", { class: "empty-hint", text: data.note || "" }));
}

/* Sanction proposals: what a staff member asked for and cannot issue alone. */
async function drawProposals() {
  const done = await call("panels/moderation/actions/proposals/", { body: {} });
  const wrap = node("div");
  if (!report(done)) return wrap;
  const data = done.payload.result || {};
  const rows = data.rows || [];

  wrap.append(section("Proposals"));
  if (!rows.length) {
    wrap.append(empty("NOBODY HAS ASKED FOR A PERMANENT BAN."));
    return wrap;
  }

  wrap.append(
    dataTable(
      ["SUBJECT", "LEVEL", "REASON", "REACHES", "ASKED BY", ""],
      rows.map((row) => [
        cell(row.subject_type + " " + row.subject_value),
        cell(row.level),
        cell(row.reason),
        node("td", {
          class: (row.collateral || {}).account_count > 1 ? "num fail-text" : "num",
          text: String((row.collateral || {}).account_count ?? 0),
        }),
        cell(row.proposed_by),
        node("td", {}, [
          node("button", {
            type: "button",
            text: "ACCEPT",
            disabled: !data.may_decide || row.yours,
            title: row.yours
              ? "You cannot decide your own proposal."
              : data.may_decide
                ? "Issue the ban this proposal asks for."
                : "This needs the permanent-ban permission.",
            onclick: () => decideProposal("approve", row.id),
          }),
          node("button", {
            type: "button",
            text: "REFUSE",
            disabled: !data.may_decide || row.yours,
            onclick: () => decideProposal("decline", row.id),
          }),
          node("button", {
            type: "button",
            text: "CANCEL",
            disabled: !row.yours,
            title: "Take back a proposal that you made.",
            onclick: () => decideProposal("withdraw", row.id),
          }),
        ]),
      ]),
    ),
  );
  wrap.append(node("p", { class: "empty-hint", text: data.note || "" }));
  return wrap;
}

async function decideProposal(action, id) {
  const note = prompt("Enter the reason for your decision.");
  if (!note) return;
  const body = action === "withdraw" ? { proposal_id: id, note } : { proposal_id: id, note };
  const done = await call("panels/moderation/actions/" + action + "/", { body });
  if (report(done)) select_render();
}

async function drawAuthorization() {
  const query = new URLSearchParams({ view: state.authView || "grants" });
  const result = await call("panels/authorization/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};

  const views = node("select", {
    id: "auth-view",
    onchange: (event) => {
      state.authView = event.target.value;
      select_render();
    },
  });
  for (const option of data.views || []) {
    views.append(
      node("option", { value: option, selected: option === data.view, text: option.toUpperCase() }),
    );
  }

  const body = node("div", { class: "panel-body" });
  body.append(prober(data));
  body.append(section(data.model || ""));

  if ((data.rows || []).length === 0) {
    body.append(empty("NO ROWS."));
  } else {
    const shown = (data.fields || []).slice(0, 7);
    const table = node("table");
    table.append(
      node("thead", {}, [node("tr", {}, shown.map((f) => node("th", { scope: "col", text: f })))]),
    );
    const tbody = node("tbody");
    for (const row of data.rows) {
      tbody.append(node("tr", {}, shown.map((f) => cell(row[f]))));
    }
    table.append(tbody);
    body.append(table);
  }

  el.station.textContent = "";
  el.station.append(
    head("AUTHORIZATION", data.row_count ? data.row_count + " ROWS" : ""),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "auth-view", text: "View" }),
        views,
      ]),
      node("span", { class: "spacer" }),
      node("span", { class: "legend", text: (data.capabilities || []).length + " CAPABILITIES" }),
    ]),
    body,
  );
}

/* Ask the resolver rather than reading grant rows and simulating it in your
 * head. The verdict is one lamp; the sentence beside it is the point. */
function prober(data) {
  const wrap = node("div", { class: "editor" });
  const principal = node("input", {
    type: "text",
    placeholder: "ACCOUNT ID",
    "aria-label": "Account id",
  });
  const capability = node("input", {
    type: "text",
    placeholder: "CAPABILITY",
    "aria-label": "Capability",
    list: "capability-list",
  });
  const options = node("datalist", { id: "capability-list" });
  for (const item of data.capabilities || []) {
    options.append(node("option", { value: item.key }));
  }
  const verdict = node("div", { class: "probe-verdict" });

  wrap.append(
    node("div", { class: "editor-head" }, [
      node("span", { class: "legend", text: "WHY CAN THIS ACCOUNT DO THAT" }),
    ]),
    node("div", { class: "fault-actions" }, [
      principal,
      capability,
      options,
      node("button", {
        type: "button",
        text: "ASK",
        onclick: async () => {
          const done = await call("panels/authorization/actions/probe/", {
            body: {
              principal_id: Number(principal.value),
              capability: capability.value,
            },
          });
          verdict.textContent = "";
          if (!report(done)) return;
          const answer = done.payload.result || {};
          verdict.append(
            lamp(answer.allowed ? "ALLOWED" : "DENIED", answer.allowed ? "ok" : "fail"),
            node("span", { class: "probe-text", text: answer.explanation || "" }),
          );
          for (const grant of answer.matching_grants || []) {
            verdict.append(
              node("div", { class: "log-line" }, [
                node("span", { class: "log-source", text: grant.scope_kind }),
                node("span", { class: "log-text", text: grant.scope_key + "  from " + grant.origin }),
              ]),
            );
          }
        },
      }),
    ]),
    verdict,
  );
  return wrap;
}

/* Proof of presence. A capability says who you are; this says you are here,
 * which is the only thing between an unlocked laptop and a REPL. The server
 * demands it independently -- this only saves the operator a round trip into a
 * refusal they can do nothing about. */
async function confirmPresence() {
  const password = prompt("Confirm your password to continue.");
  if (!password) return false;
  const result = await call("confirm/", { body: { password } });
  if (!report(result)) return false;
  state.confirmed = true;
  return true;
}

/* A panel a deployment has not switched on. Distinct from a refusal about who
 * you are, so the message names the setting rather than implying you lack
 * standing. */
function disabledNotice(data) {
  return node("div", { class: "empty" }, [
    node("p", { class: "empty-line", text: "THIS PANEL IS SWITCHED OFF." }),
    node("p", {
      class: "empty-hint",
      text: "To use this panel, set " + data.setting + " to True in the game settings.",
    }),
  ]);
}

async function drawRepl() {
  const result = await call("panels/repl/rows/");
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (!data.enabled) {
    body.append(disabledNotice(data));
  } else {
    const source = node("textarea", {
      id: "repl-source",
      rows: "6",
      spellcheck: "false",
      "aria-label": "Python to run",
      placeholder: "PYTHON. EVERY SUBMISSION IS RECORDED WITH ITS SOURCE.",
    });
    source.value = state.replSource || "";
    const output = node("div", { class: "log-view", id: "repl-output" });

    const run = node("button", {
      type: "button",
      text: "RUN",
      onclick: async () => {
        state.replSource = source.value;
        if (!state.confirmed && !(await confirmPresence())) return;
        const done = await call("panels/repl/actions/execute/", {
          body: { source: source.value },
        });
        output.textContent = "";
        if (!report(done)) {
          state.confirmed = false;
          return;
        }
        const payload = done.payload.result || {};
        for (const line of payload.output || []) {
          output.append(node("div", { class: "log-line" }, [
            node("span", { class: "log-text", text: line }),
          ]));
        }
        if (payload.message) {
          output.append(node("div", { class: "log-line" }, [
            node("span", { class: "log-text", style: "color:var(--fail)", text: payload.message }),
          ]));
        }
        output.scrollTop = output.scrollHeight;
      },
    });

    body.append(
      node("div", { class: "editor" }, [source, node("div", { class: "fault-actions" }, [run])]),
      section("Output"),
      output,
    );

    if ((data.history || []).length) {
      body.append(section("Your recent submissions"));
      const history = node("div", { class: "log-view" });
      for (const item of data.history) {
        history.append(node("div", { class: "log-line" }, [
          node("span", { class: "log-source", text: item.outcome }),
          node("span", { class: "log-text", text: item.source }),
        ]));
      }
      body.append(history);
    }
  }

  el.station.textContent = "";
  el.station.append(
    head("REPL"),
    node("div", { class: "toolbar" }, [
      lamp(data.enabled ? "ENABLED" : "DISABLED", data.enabled ? "attn" : "off"),
      node("span", { class: "legend", text: data.note || "" }),
    ]),
    body,
  );
}

async function drawSql() {
  const result = await call("panels/sql/rows/");
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (!data.enabled) {
    body.append(disabledNotice(data));
  } else {
    const text = node("textarea", {
      id: "sql-text",
      rows: "4",
      spellcheck: "false",
      "aria-label": "Read-only SQL",
      placeholder: "READ-ONLY SQL. ALLOWED: " + (data.allowed || []).join(", ").toUpperCase(),
    });
    text.value = state.sqlText || "";
    const results = node("div", { id: "sql-results" });

    const run = node("button", {
      type: "button",
      text: "RUN",
      onclick: async () => {
        state.sqlText = text.value;
        if (!state.confirmed && !(await confirmPresence())) return;
        const done = await call("panels/sql/actions/query/", { body: { sql: text.value } });
        results.textContent = "";
        if (!report(done)) {
          state.confirmed = false;
          return;
        }
        const payload = done.payload.result || {};
        if (payload.message) {
          results.append(empty("THE QUERY DID NOT RUN.", payload.message));
          return;
        }
        const table = node("table");
        table.append(node("thead", {}, [
          node("tr", {}, (payload.columns || []).map((c) => node("th", { scope: "col", text: c }))),
        ]));
        const tbody = node("tbody");
        for (const row of payload.rows || []) {
          tbody.append(node("tr", {}, row.map((value) => cell(value))));
        }
        table.append(tbody);
        results.append(table);
        if (payload.capped) {
          results.append(node("p", {
            class: "empty-hint",
            text: "Capped at " + data.max_rows + " rows. Narrow the query to see more.",
          }));
        }
      },
    });

    body.append(
      node("div", { class: "editor" }, [text, node("div", { class: "fault-actions" }, [run])]),
      results,
    );
  }

  el.station.textContent = "";
  el.station.append(
    head("SQL"),
    node("div", { class: "toolbar" }, [
      lamp(data.enabled ? "ENABLED" : "DISABLED", data.enabled ? "attn" : "off"),
      node("span", { class: "legend", text: data.enabled ? data.timeout_ms + "MS TIMEOUT, " + data.max_rows + " ROW CAP" : "" }),
    ]),
    body,
  );
}

async function drawServer() {
  const result = await call("panels/server/rows/");
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  const checks = node("dl", { class: "rows" });
  for (const [name, ok] of Object.entries(data.checks || {})) {
    checks.append(node("div", { class: "row-pair" }, [
      node("dt", { text: name.replace(/_/g, " ") }),
      node("dd", {}, [lamp(ok ? "OK" : "FAILED", ok ? "ok" : "fail")]),
    ]));
  }
  checks.append(node("div", { class: "row-pair" }, [
    node("dt", { text: "version" }),
    node("dd", { text: data.version || "--" }),
  ]));
  body.append(section("Status"), checks);

  body.append(section("Control"));
  if (!data.enabled) {
    body.append(disabledNotice(data));
  } else {
    const controls = node("div", { class: "fault-actions" });
    for (const action of data.actions || []) {
      controls.append(node("button", {
        type: "button",
        text: action.toUpperCase(),
        onclick: async () => {
          const reason = prompt("Why is the server being told to " + action + "?");
          if (!reason) return;
          if (!state.confirmed && !(await confirmPresence())) return;
          const done = await call("panels/server/actions/control/", { body: { action, reason } });
          if (!report(done)) state.confirmed = false;
        },
      }));
    }
    body.append(controls);
    body.append(node("p", { class: "empty-hint", text: data.note || "" }));
  }

  el.station.textContent = "";
  el.station.append(
    head("SERVER CONTROL"),
    node("div", { class: "toolbar" }, [
      lamp(data.degraded ? "DEGRADED" : "RUNNING", data.degraded ? "attn" : "ok"),
      lamp(data.enabled ? "CONTROL ENABLED" : "CONTROL DISABLED", data.enabled ? "attn" : "off"),
    ]),
    body,
  );
}

async function drawSessions() {
  const result = await call("panels/sessions/rows/");
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (!data.available) {
    body.append(empty("THE SESSION HANDLER IS NOT LOADED.", data.reason || ""));
  } else if ((data.rows || []).length === 0) {
    body.append(empty("NOBODY IS CONNECTED."));
  } else {
    const table = node("table");
    table.append(node("thead", {}, [
      node("tr", {}, [
        node("th", { scope: "col", text: "ACCOUNT" }),
        node("th", { scope: "col", text: "PUPPET" }),
        node("th", { scope: "col", text: "PROTOCOL" }),
        node("th", { scope: "col", text: "COMMANDS" }),
        node("th", { scope: "col", text: "" }),
      ]),
    ]));
    const tbody = node("tbody");
    for (const row of data.rows) {
      tbody.append(node("tr", {}, [
        cell(row.account),
        cell(row.puppet),
        cell(row.protocol),
        node("td", { class: "num", text: String(row.commands) }),
        node("td", {}, [
          node("button", {
            type: "button",
            text: "WATCH",
            title: "The console records this action permanently. The account sees it in its own timeline.",
            onclick: () => sessionAction("watch", row.sessid, "Why is this session being watched?"),
          }),
          node("button", {
            type: "button",
            text: "DISCONNECT",
            onclick: () => sessionAction("disconnect", row.sessid, "Why is this session being disconnected?"),
          }),
        ]),
      ]));
    }
    table.append(tbody);
    body.append(table);
  }

  el.station.textContent = "";
  el.station.append(
    head("LIVE SESSIONS", data.count ? data.count + " CONNECTED" : ""),
    node("div", { class: "toolbar" }, [
      node("span", { class: "legend", text: data.note || "" }),
    ]),
    body,
  );
}

async function sessionAction(action, sessid, question) {
  const reason = prompt(question);
  if (!reason) return;
  if (action === "watch" && !state.confirmed && !(await confirmPresence())) return;
  const done = await call("panels/sessions/actions/" + action + "/", { body: { sessid, reason } });
  if (report(done)) select_render();
  else state.confirmed = false;
}

/* A search box that writes one state key and redraws. Four stations wanted the
 * same control, and four hand-rolled copies drift. */
function searchField(key, label) {
  return node("div", { class: "field" }, [
    node("input", {
      type: "search",
      value: state[key] || "",
      placeholder: label,
      "aria-label": label,
      onchange: (event) => {
        state[key] = event.target.value;
        select_render();
      },
    }),
  ]);
}

function dataTable(headings, rows) {
  const element = node("table");
  element.append(
    node("thead", {}, [
      node("tr", {}, headings.map((text) => node("th", { scope: "col", text }))),
    ]),
  );
  const body = node("tbody");
  for (const row of rows) body.append(node("tr", {}, row));
  element.append(body);
  return element;
}

async function drawObjects() {
  const query = new URLSearchParams({ search: state.objSearch || "" });
  const result = await call("panels/objects/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  /* Orphans first, and only when there are some. A stored path that no longer
   * imports is the one finding on this station that needs an operator; the
   * rest is reference. */
  const orphans = data.orphans || [];
  if (orphans.length) {
    body.append(section("Paths that do not import"));
    body.append(
      dataTable(
        ["PATH", "ROWS"],
        orphans.map((row) => [
          node("td", { class: "fail-text", text: row.path }),
          node("td", { class: "num", text: String(row.instances) }),
        ]),
      ),
    );
    body.append(
      node("p", {
        class: "empty-hint",
        text: "These rows still load through a fallback. Nothing else reports this.",
      }),
    );
  }

  body.append(section("Stored"));
  const stored = data.stored || [];
  if (!stored.length) {
    body.append(empty("NO ROWS CARRY A TYPECLASS PATH."));
  } else {
    body.append(
      dataTable(
        ["PATH", "ROWS", "IMPORTS"],
        stored.map((row) => [
          cell(row.path),
          node("td", { class: "num", text: String(row.instances) }),
          node("td", {}, [
            lamp(row.importable ? "LOADED" : "NOT LOADED", row.importable ? "ok" : "off"),
          ]),
        ]),
      ),
    );
  }
  body.append(node("p", { class: "empty-hint", text: data.note || "" }));

  body.append(section("Loaded classes"));
  const rows = data.rows || [];
  if (!rows.length) {
    body.append(empty("NO CLASS MATCHES THIS SEARCH."));
  } else {
    body.append(
      dataTable(
        ["CLASS", "PATH", "PARENT", "ROWS"],
        rows.map((row) => [
          cell(row.name),
          cell(row.path),
          cell(row.base),
          node("td", { class: "num", text: row.instances === null ? "--" : String(row.instances) }),
        ]),
      ),
    );
  }
  body.append(node("p", { class: "empty-hint", text: data.importable_caveat || "" }));

  el.station.textContent = "";
  el.station.append(
    head("OBJECTS", data.typeclass_count ? data.typeclass_count + " CLASSES" : ""),
    node("div", { class: "toolbar" }, [
      searchField("objSearch", "FILTER BY CLASS OR PATH"),
      node("span", { class: "spacer" }),
      orphans.length
        ? lamp(orphans.length + " DO NOT IMPORT", "fail")
        : lamp("ALL STORED PATHS IMPORT", "ok"),
    ]),
    body,
  );
}

async function drawActions() {
  const query = new URLSearchParams({ search: state.actionSearch || "" });
  const result = await call("panels/actions/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (!data.available) {
    body.append(empty("THE ACTION REGISTRY IS NOT LOADED.", data.reason || ""));
    el.station.textContent = "";
    el.station.append(head("ACTIONS"), body);
    return;
  }

  /* The question an operator has is "why did that not work". Answering it by
   * reading the verb trie by hand is what this replaces. Nothing runs. */
  body.append(section("Resolve one line"));
  const input = node("input", {
    type: "text",
    id: "action-probe",
    placeholder: "A LINE OF PLAYER INPUT, FOR EXAMPLE: DROP SWORD",
    "aria-label": "Player input to resolve",
    spellcheck: "false",
  });
  const verdict = node("div", { id: "action-verdict" });

  const paint = () => {
    verdict.textContent = "";
    const probe = state.actionProbe;
    if (!probe) return;
    verdict.append(
      node("p", {}, [
        lamp(probe.matched ? "MATCH" : "NO MATCH", probe.matched ? "ok" : "attn"),
        node("span", { class: "legend", text: " " + probe.explanation }),
      ]),
    );
    if (probe.matched) {
      const pairs = node("dl", { class: "rows" });
      for (const [label, value] of [
        ["action", probe.matched.action],
        ["module", probe.matched.module],
        ["matched verb", probe.matched.verb],
        ["score", probe.matched.score === null ? "--" : String(probe.matched.score)],
      ]) {
        pairs.append(
          node("div", { class: "row-pair" }, [
            node("dt", { text: label }),
            node("dd", { text: value }),
          ]),
        );
      }
      verdict.append(pairs);
    } else if ((probe.suggestions || []).length) {
      verdict.append(
        node("p", { class: "empty-hint", text: "Near it: " + probe.suggestions.join(", ") }),
      );
    }
  };

  const run = async () => {
    const text = input.value.trim();
    if (!text) return;
    const done = await call("panels/actions/actions/resolve/", { body: { text } });
    if (!report(done)) return;
    state.actionProbe = done.payload.result || null;
    paint();
  };

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") run();
  });
  body.append(
    node("div", { class: "toolbar" }, [
      node("div", { class: "field grow" }, [input]),
      node("button", { type: "button", text: "RESOLVE", onclick: run }),
    ]),
    verdict,
  );
  paint();

  body.append(section("Registered"));
  const rows = data.rows || [];
  if (!rows.length) {
    body.append(empty("NO ACTION MATCHES THIS SEARCH."));
  } else {
    body.append(
      dataTable(
        ["ACTION", "VERBS", "CAPABILITY", "SUMMARY"],
        rows.map((row) => [
          cell(row.name),
          cell((row.verbs || []).join(" ")),
          cell(row.capability),
          cell(row.summary),
        ]),
      ),
    );
  }
  body.append(node("p", { class: "empty-hint", text: data.note || "" }));

  el.station.textContent = "";
  el.station.append(
    head("ACTIONS", data.action_count ? data.action_count + " ACTIONS" : ""),
    node("div", { class: "toolbar" }, [
      searchField("actionSearch", "FILTER BY ACTION OR VERB"),
      node("span", { class: "spacer" }),
      lamp((data.verbs || []).length + " VERBS", "off"),
    ]),
    body,
  );
}

async function drawHooks() {
  const query = new URLSearchParams({
    event: state.hookEvent || "",
    search: state.hookSearch || "",
  });
  const result = await call("panels/hooks/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (!data.available) {
    body.append(empty("THE HOOK REGISTRY IS NOT LOADED.", data.reason || ""));
    el.station.textContent = "";
    el.station.append(head("HOOKS"), body);
    return;
  }

  const findings = data.findings || [];
  if (findings.length) {
    body.append(section("Lint findings"));
    body.append(
      dataTable(
        ["HOOK", "PROBLEM"],
        findings.map((row) => [
          node("td", { class: "fail-text", text: row.name }),
          cell(row.problem),
        ]),
      ),
    );
  }

  body.append(section("Declared"));
  const rows = data.rows || [];
  if (!rows.length) {
    body.append(empty("NO HOOK MATCHES THIS FILTER."));
  } else {
    body.append(
      dataTable(
        ["HOOK", "EVENT", "PHASE", "RETURNS", "DISCIPLINE"],
        rows.map((row) => [
          cell(row.name),
          cell(row.event),
          cell(row.phase),
          cell(row.returns),
          cell(row.discipline),
        ]),
      ),
    );
  }
  body.append(node("p", { class: "empty-hint", text: data.note || "" }));

  const picker = node("select", {
    id: "hook-event",
    "aria-label": "Filter by event",
    onchange: (event) => {
      state.hookEvent = event.target.value;
      select_render();
    },
  });
  picker.append(node("option", { value: "", text: "ALL EVENTS", selected: !state.hookEvent }));
  for (const event of data.events || []) {
    picker.append(node("option", { value: event, selected: event === state.hookEvent, text: event }));
  }

  el.station.textContent = "";
  el.station.append(
    head("HOOKS", data.hook_count ? data.hook_count + " HOOKS" : ""),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "hook-event", text: "Event" }),
        picker,
      ]),
      searchField("hookSearch", "FILTER BY HOOK OR EVENT"),
      node("span", { class: "spacer" }),
      findings.length ? lamp(findings.length + " FINDINGS", "fail") : lamp("LINT CLEAN", "ok"),
    ]),
    body,
  );
}

async function drawPrototypes() {
  const query = new URLSearchParams({ search: state.protoSearch || "" });
  const result = await call("panels/prototypes/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (!data.available) {
    body.append(empty("PROTOTYPES ARE NOT LOADED.", data.reason || ""));
  } else if (!(data.rows || []).length) {
    body.append(empty("THIS GAME DECLARES NO PROTOTYPE."));
  } else {
    body.append(
      dataTable(
        ["KEY", "TYPECLASS", "PARENT", "FIELDS"],
        data.rows.map((row) => [
          cell(row.key),
          cell(row.typeclass),
          cell(row.parent),
          cell((row.fields || []).join(" ")),
        ]),
      ),
    );
  }
  body.append(node("p", { class: "empty-hint", text: data.note || "" }));

  el.station.textContent = "";
  el.station.append(
    head("PROTOTYPES", data.count ? data.count + " PROTOTYPES" : ""),
    node("div", { class: "toolbar" }, [
      searchField("protoSearch", "FILTER BY KEY"),
      node("span", { class: "spacer" }),
      lamp("READ ONLY", "off"),
    ]),
    body,
  );
}

/* The five outcomes must not render alike. A partial and a recovery_required
 * both mean "it wrote, then faulted", and the difference between them is
 * whether a person has to go fix something. */
const OUTCOME_STATE = {
  success: "ok",
  conflict: "off",
  partial: "fail",
  recovery_required: "fail",
  indeterminate: "attn",
};

async function drawAudit() {
  const query = new URLSearchParams({
    outcome: state.auditOutcome || "",
    panel: state.auditPanel || "",
    actor: state.auditActor || "",
    target: state.auditTarget || "",
    cursor: state.auditCursor || "",
  });
  const result = await call("panels/audit/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  const rows = data.rows || [];
  if (!rows.length) {
    body.append(empty("NO RECORDED OPERATION MATCHES THIS FILTER."));
  } else {
    body.append(
      dataTable(
        ["WHEN", "OPERATOR", "OPERATION", "OUTCOME", "TARGET", ""],
        rows.map((row) => [
          cell(row.created_at.replace("T", " ").slice(0, 19)),
          cell(row.actor_name),
          cell(row.panel + "." + row.operation),
          node("td", {}, [lamp(row.outcome.toUpperCase(), OUTCOME_STATE[row.outcome] || "off")]),
          cell(row.target_ref),
          node("td", {}, [
            node("button", {
              type: "button",
              text: "OPEN",
              onclick: async () => {
                state.auditOpen = row.id;
                await select_render();
              },
            }),
          ]),
        ]),
      ),
    );
  }

  if (data.next_cursor) {
    body.append(
      node("div", { class: "fault-actions" }, [
        node("button", {
          type: "button",
          text: "NEXT PAGE",
          onclick: () => {
            state.auditCursor = data.next_cursor;
            select_render();
          },
        }),
      ]),
    );
  }
  body.append(node("p", { class: "empty-hint", text: data.note || "" }));

  if (state.auditOpen) {
    body.prepend(await auditDetail(state.auditOpen));
  }

  const outcomes = node("select", {
    id: "audit-outcome",
    "aria-label": "Filter by outcome",
    onchange: (event) => {
      state.auditOutcome = event.target.value;
      state.auditCursor = "";
      select_render();
    },
  });
  outcomes.append(
    node("option", { value: "", text: "ALL OUTCOMES", selected: !state.auditOutcome }),
  );
  for (const item of data.outcomes || []) {
    outcomes.append(
      node("option", {
        value: item.value,
        selected: item.value === state.auditOutcome,
        text: item.value.toUpperCase() + " - " + item.meaning,
      }),
    );
  }

  const panels = node("select", {
    id: "audit-panel",
    "aria-label": "Filter by panel",
    onchange: (event) => {
      state.auditPanel = event.target.value;
      state.auditCursor = "";
      select_render();
    },
  });
  panels.append(node("option", { value: "", text: "ALL PANELS", selected: !state.auditPanel }));
  for (const item of data.panels || []) {
    panels.append(
      node("option", { value: item, selected: item === state.auditPanel, text: item }),
    );
  }

  el.station.textContent = "";
  el.station.append(
    head("AUDIT", rows.length ? rows.length + " SHOWN" : ""),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "audit-outcome", text: "Outcome" }),
        outcomes,
      ]),
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "audit-panel", text: "Panel" }),
        panels,
      ]),
      searchField("auditActor", "OPERATOR NAME OR ID"),
      searchField("auditTarget", "TARGET PREFIX"),
    ]),
    body,
  );
}

async function auditDetail(id) {
  const result = await call("panels/audit/detail/" + encodeURIComponent(id) + "/");
  const wrap = node("div", { class: "detail" });
  if (!report(result)) return wrap;
  const data = result.payload.record || {};

  const facts = node("dl", { class: "rows" });
  for (const [label, value] of [
    ["event", data.event_id],
    ["when", (data.created_at || "").replace("T", " ").slice(0, 19)],
    ["operator", data.actor_name],
    ["operation", data.panel + "." + data.operation],
    ["target", data.target_ref],
    ["outcome", data.outcome + " - " + data.outcome_meaning],
    ["retryable", data.retryable ? "yes" : "no"],
    ["retention", data.retention + " - " + data.retention_meaning],
    ["correlation", data.correlation_id || "--"],
    ["message", data.message || "--"],
  ]) {
    facts.append(
      node("div", { class: "row-pair" }, [
        node("dt", { text: label }),
        node("dd", { text: String(value ?? "") }),
      ]),
    );
  }

  const diff = data.diff || {};
  const diffTable = (diff.entries || []).length
    ? dataTable(
        ["FIELD", "BEFORE", "AFTER"],
        diff.entries.map((entry) => [
          node("td", { class: entry.changed ? "fail-text" : "", text: entry.field }),
          cell(entry.before),
          cell(entry.after),
        ]),
      )
    : empty("THIS OPERATION RECORDED NO FIELD STATE.");

  const actions = node("div", { class: "fault-actions" });
  if (data.can_undo) {
    actions.append(
      node("button", {
        type: "button",
        text: "UNDO",
        onclick: async () => {
          const reason = prompt("Why is this operation being reversed?");
          if (!reason) return;
          const done = await call("panels/audit/actions/undo/", {
            body: { audit_id: data.id, reason },
          });
          if (report(done)) {
            state.auditCursor = "";
            select_render();
          }
        },
      }),
    );
  } else {
    actions.append(node("p", { class: "empty-hint", text: data.undo_reason || "" }));
  }

  /* State as of a moment. Not event sourcing and no new store: it folds the
   * audit table backwards from the newest recorded values. */
  const asOf = node("div", { id: "as-of" });
  const stamp = node("input", {
    type: "datetime-local",
    id: "as-of-when",
    "aria-label": "Date and time to reconstruct",
  });
  const reconstruct = node("button", {
    type: "button",
    text: "SHOW VALUES AT THIS TIME",
    onclick: async () => {
      if (!stamp.value) return;
      const done = await call("panels/audit/actions/state_as_of/", {
        body: { target: data.target_ref, when: stamp.value },
      });
      asOf.textContent = "";
      if (!report(done)) return;
      const state_ = done.payload.result || {};
      const fields = Object.entries(state_.fields || {});
      asOf.append(
        node("p", {}, [
          lamp(state_.covered ? "COMPLETE" : "INCOMPLETE", state_.covered ? "ok" : "attn"),
          node("span", {
            class: "legend",
            text:
              " " +
              (state_.reason ||
                "The console undid " + state_.changes_undone + " change(s)."),
          }),
        ]),
      );
      asOf.append(
        fields.length
          ? dataTable(
              ["FIELD", "VALUE AT THIS TIME"],
              fields.map(([name, value]) => [cell(name), cell(value)]),
            )
          : empty("THE CONSOLE HAS NO RECORDED VALUES FOR THIS RECORD."),
      );
      if (state_.note) asOf.append(node("p", { class: "empty-hint", text: state_.note }));
    },
  });

  wrap.append(
    node("div", { class: "toolbar" }, [
      node("span", { class: "legend", text: "RECORDED OPERATION" }),
      node("span", { class: "spacer" }),
      node("button", {
        type: "button",
        text: "CLOSE",
        onclick: () => {
          state.auditOpen = null;
          select_render();
        },
      }),
    ]),
    facts,
    section("Field state"),
    diffTable,
    diff.truncated
      ? node("p", {
          class: "empty-hint",
          text: "The record contains " + diff.truncated + " more fields. This page does not show them.",
        })
      : null,
    actions,
    section("Values at a past time"),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "as-of-when", text: "Time" }),
        stamp,
      ]),
      reconstruct,
    ]),
    asOf,
  );
  return wrap;
}

function bytes(value) {
  const size = Number(value || 0);
  if (size < 1024) return size + " B";
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
  if (size < 1024 * 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + " MB";
  return (size / (1024 * 1024 * 1024)).toFixed(2) + " GB";
}

function picker(id, label, values, key, blank) {
  const element = node("select", {
    id,
    "aria-label": label,
    onchange: (event) => {
      state[key] = event.target.value;
      select_render();
    },
  });
  element.append(node("option", { value: "", text: blank, selected: !state[key] }));
  for (const value of values || []) {
    element.append(
      node("option", { value, selected: value === state[key], text: String(value) }),
    );
  }
  return node("div", { class: "field" }, [
    node("label", { class: "legend", for: id, text: label }),
    element,
  ]);
}

async function drawJobs() {
  const query = new URLSearchParams({
    status: state.jobStatus || "",
    job_type: state.jobType || "",
  });
  const result = await call("panels/jobs/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  body.append(
    annunciator(
      [
        data.dead && { text: data.dead + " DEAD LETTERS", state: "fail" },
        data.overdue_leases && {
          text: data.overdue_leases + " LEASES OUT OF DATE",
          state: "attn",
        },
      ],
      "THE QUEUE IS DRAINING",
    ),
  );

  /* Depth by status, as blocks that are also the filter. */
  body.append(
    countBlocks(
      (data.by_status || []).map((row) => ({
        key: row.status,
        label: row.status,
        count: row.total,
        wants: row.status === "dead" ? row.total : 0,
        sub: row.status === "dead" ? "need a person" : "",
      })),
      state.jobStatus,
      (key) => {
        state.jobStatus = key;
        state.jobOpen = null;
        select_render();
      },
    ),
  );

  if (data.overdue_leases) {
    body.append(
      node("p", {
        class: "empty-hint",
        text:
          "A lease that is out of date shows that the worker stopped. The next queue run takes the job again. You do not need to do this.",
      }),
    );
  }

  if (state.jobOpen) body.append(await jobDetail(state.jobOpen));

  body.append(section("Queue"));
  const rows = data.rows || [];
  if (!rows.length) {
    body.append(empty("NO JOB MATCHES THIS FILTER."));
  } else {
    body.append(
      dataTable(
        ["TYPE", "STATUS", "ATTEMPTS", "CREATED", "LAST ERROR", ""],
        rows.map((row) => [
          cell(row.job_type),
          node("td", {}, [
            lamp(
              row.status.toUpperCase(),
              row.status === "dead" ? "fail" : row.status === "pending" ? "attn" : "ok",
            ),
          ]),
          node("td", { class: "num", text: row.attempts + "/" + row.max_attempts }),
          cell(row.created_at.replace("T", " ").slice(0, 19)),
          cell(row.last_error),
          node("td", {}, [
            node("button", {
              type: "button",
              text: "OPEN",
              onclick: () => {
                state.jobOpen = row.id;
                select_render();
              },
            }),
          ]),
        ]),
      ),
    );
  }
  body.append(node("p", { class: "empty-hint", text: data.note || "" }));

  const backend = data.backend || {};
  el.station.textContent = "";
  el.station.append(
    head("JOBS", data.dead ? data.dead + " DEAD" : ""),
    node("div", { class: "toolbar" }, [
      picker("job-status", "Status", (data.by_status || []).map((row) => row.status), "jobStatus", "ALL STATUSES"),
      picker("job-type", "Type", data.types, "jobType", "ALL TYPES"),
      node("span", { class: "spacer" }),
      lamp(
        (backend.backend || "NO BACKEND").toUpperCase(),
        backend.enabled ? "ok" : "off",
      ),
    ]),
    body,
  );
}

async function jobDetail(id) {
  const wrap = node("div", { class: "detail" });
  const result = await call("panels/jobs/detail/" + encodeURIComponent(id) + "/");
  wrap.append(
    node("div", { class: "toolbar" }, [
      node("span", { class: "legend", text: "JOB" }),
      node("span", { class: "spacer" }),
      node("button", {
        type: "button",
        text: "CLOSE",
        onclick: () => {
          state.jobOpen = null;
          select_render();
        },
      }),
    ]),
  );
  if (!report(result)) return wrap;
  const data = result.payload.record || {};

  const facts = node("dl", { class: "rows" });
  for (const [label, value] of [
    ["job", data.job_id],
    ["type", data.job_type],
    ["status", data.status],
    ["attempts", data.attempts + " of " + data.max_attempts],
    ["priority", data.priority],
    ["idempotency key", data.idempotency_key || "--"],
    ["created", (data.created_at || "").replace("T", " ").slice(0, 19)],
    ["available at", (data.available_at || "--").replace("T", " ").slice(0, 19)],
    ["lease until", (data.lease_until || "--").replace("T", " ").slice(0, 19)],
  ]) {
    facts.append(
      node("div", { class: "row-pair" }, [
        node("dt", { text: label }),
        node("dd", { text: String(value ?? "") }),
      ]),
    );
  }
  wrap.append(facts, section("Payload"), node("pre", { class: "code", text: data.payload || "" }));
  if (data.last_error) {
    wrap.append(section("Last error"), node("pre", { class: "code", text: data.last_error }));
  }
  if (data.can_requeue) {
    wrap.append(
      node("div", { class: "fault-actions" }, [
        node("button", {
          type: "button",
          text: "REQUEUE",
          title: "Return this job to the queue. The next queue run starts it.",
          onclick: async () => {
            const reason = prompt("Why is this job being requeued?");
            if (!reason) return;
            const done = await call("panels/jobs/actions/requeue/", {
              body: { job_id: data.id, reason },
            });
            if (report(done)) {
              state.jobOpen = null;
              select_render();
            }
          },
        }),
      ]),
    );
  } else {
    wrap.append(
      node("p", {
        class: "empty-hint",
        text: "You can requeue only a job with the status 'dead'. This job has the status " + data.status + ".",
      }),
    );
  }
  return wrap;
}

async function drawEventbus() {
  const query = new URLSearchParams({
    prefix: state.busPrefix || "",
    subject: state.busSubject || "",
    before: state.busBefore || "",
  });
  const result = await call("panels/eventbus/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (state.busOpen) body.append(await busDetail(state.busOpen));

  const rows = data.rows || [];
  if (!rows.length) {
    body.append(empty("NO RECORD MATCHES THIS FILTER."));
  } else {
    body.append(
      dataTable(
        ["WHEN", "SUBJECT", "ACTOR", ""],
        rows.map((row) => [
          cell(row.created_at.replace("T", " ").slice(0, 19)),
          cell(row.subject),
          cell(row.actor_ref),
          node("td", {}, [
            node("button", {
              type: "button",
              text: "OPEN",
              onclick: () => {
                state.busOpen = row.id;
                select_render();
              },
            }),
          ]),
        ]),
      ),
    );
  }
  if (data.next_before) {
    body.append(
      node("div", { class: "fault-actions" }, [
        node("button", {
          type: "button",
          text: "OLDER",
          onclick: () => {
            state.busBefore = data.next_before;
            select_render();
          },
        }),
      ]),
    );
  }
  body.append(node("p", { class: "empty-hint", text: data.note || "" }));

  const bus = data.bus || {};
  el.station.textContent = "";
  el.station.append(
    head("EVENT BUS", rows.length ? rows.length + " SHOWN" : ""),
    node("div", { class: "toolbar" }, [
      picker("bus-prefix", "Prefix", data.prefixes, "busPrefix", "ALL PREFIXES"),
      picker("bus-subject", "Subject", data.subjects, "busSubject", "ALL SUBJECTS"),
      node("span", { class: "spacer" }),
      lamp((bus.backend || "NO BACKEND").toUpperCase(), bus.enabled ? "ok" : "off"),
    ]),
    body,
  );
}

async function busDetail(id) {
  const wrap = node("div", { class: "detail" });
  const result = await call("panels/eventbus/detail/" + encodeURIComponent(id) + "/");
  wrap.append(
    node("div", { class: "toolbar" }, [
      node("span", { class: "legend", text: "BUS RECORD" }),
      node("span", { class: "spacer" }),
      node("button", {
        type: "button",
        text: "CLOSE",
        onclick: () => {
          state.busOpen = null;
          select_render();
        },
      }),
    ]),
  );
  if (!report(result)) return wrap;
  const data = result.payload.record || {};
  wrap.append(
    node("dl", { class: "rows" }, [
      node("div", { class: "row-pair" }, [
        node("dt", { text: "subject" }),
        node("dd", { text: data.subject || "" }),
      ]),
      node("div", { class: "row-pair" }, [
        node("dt", { text: "actor" }),
        node("dd", { text: data.actor_ref || "--" }),
      ]),
      node("div", { class: "row-pair" }, [
        node("dt", { text: "when" }),
        node("dd", { text: (data.created_at || "").replace("T", " ").slice(0, 19) }),
      ]),
    ]),
    section("Payload"),
    node("pre", { class: "code", text: data.payload || "" }),
  );
  return wrap;
}

async function drawDatabase() {
  const result = await call("panels/database/rows/");
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (!data.supported) {
    body.append(empty("THIS BACKEND CANNOT ANSWER.", data.reason || ""));
    el.station.textContent = "";
    el.station.append(
      head("DATABASE"),
      node("div", { class: "toolbar" }, [
        lamp((data.vendor || "UNKNOWN").toUpperCase(), "off"),
      ]),
      body,
    );
    return;
  }

  const connections = data.connections || {};
  const conn = node("dl", { class: "rows" });
  for (const row of connections.by_state || []) {
    conn.append(
      node("div", { class: "row-pair" }, [
        node("dt", { text: row.state }),
        node("dd", { class: "num", text: String(row.total) }),
      ]),
    );
  }
  conn.append(
    node("div", { class: "row-pair" }, [
      node("dt", { text: "headroom" }),
      node("dd", {}, [
        lamp(
          connections.headroom + " OF " + connections.max_connections,
          connections.headroom > 10 ? "ok" : "fail",
        ),
      ]),
    ]),
  );
  body.append(section("Connections"), conn);

  const slow = data.long_running || [];
  body.append(section("Long-running statements"));
  body.append(
    slow.length
      ? dataTable(
          ["PID", "SECONDS", "STATE", "STATEMENT"],
          slow.map((row) => [
            node("td", { class: "num", text: String(row.pid) }),
            node("td", { class: "num fail-text", text: String(row.seconds) }),
            cell(row.state),
            cell(row.query),
          ]),
        )
      : empty("NOTHING IS RUNNING LONG."),
  );

  body.append(section("Tables"));
  body.append(
    dataTable(
      ["TABLE", "ROWS", "TOTAL", "HEAP", "INDEXES", "DEAD ROWS", "LAST VACUUM"],
      (data.tables || []).map((row) => [
        cell(row.table),
        node("td", { class: "num", text: String(row.rows) }),
        node("td", { class: "num", text: bytes(row.total_bytes) }),
        node("td", { class: "num", text: bytes(row.heap_bytes) }),
        node("td", { class: "num", text: bytes(row.index_bytes) }),
        node("td", { class: "num", text: String(row.dead_rows) }),
        cell(String(row.last_autovacuum || row.last_vacuum || "never").slice(0, 19)),
      ]),
    ),
  );
  body.append(node("p", { class: "empty-hint", text: data.note || "" }));

  const unused = data.unused_indexes || {};
  body.append(section("Indexes never scanned"));
  body.append(
    (unused.rows || []).length
      ? dataTable(
          ["TABLE", "INDEX", "SIZE"],
          unused.rows.map((row) => [
            cell(row.table),
            node("td", { class: "fail-text", text: row.index }),
            node("td", { class: "num", text: bytes(row.bytes) }),
          ]),
        )
      : empty("EVERY INDEX HAS BEEN SCANNED."),
  );
  body.append(
    node("p", {
      class: "empty-hint",
      text: (unused.note || "") + " Statistics reset: " + (unused.stats_reset || "unknown") + ".",
    }),
  );

  body.append(section("Index usage"));
  body.append(
    dataTable(
      ["TABLE", "INDEX", "SCANS", "TUPLES READ", "SIZE"],
      (data.indexes || []).map((row) => [
        cell(row.table),
        cell(row.index),
        node("td", { class: "num", text: String(row.scans) }),
        node("td", { class: "num", text: String(row.tuples_read) }),
        node("td", { class: "num", text: bytes(row.bytes) }),
      ]),
    ),
  );

  body.append(section("Attribute document sizes"), await dbSizes());

  el.station.textContent = "";
  el.station.append(
    head("DATABASE"),
    node("div", { class: "toolbar" }, [
      lamp((data.vendor || "").toUpperCase(), "ok"),
      lamp((data.tables || []).length + " TABLES", "off"),
    ]),
    body,
  );
}

async function dbSizes() {
  const wrap = node("div");
  const listing = await call("panels/database/actions/models/", { body: {} });
  if (!report(listing)) return wrap;
  const models = (listing.payload.result || {}).models || [];
  if (!models.length) return wrap;
  if (!state.dbModel) state.dbModel = models[0];

  wrap.append(
    node("div", { class: "toolbar" }, [
      picker("db-model", "Model", models, "dbModel", "CHOOSE A MODEL"),
    ]),
  );

  const result = await call("panels/database/actions/sizes/", {
    body: { model: state.dbModel },
  });
  if (!report(result)) return wrap;
  const data = result.payload.result || {};
  wrap.append(
    node("dl", { class: "rows" }, [
      node("div", { class: "row-pair" }, [
        node("dt", { text: "documents measured" }),
        node("dd", {}, [
          node("span", { class: "num", text: String(data.sampled ?? 0) }),
          node("span", {
            class: "empty-hint",
            text: data.complete ? " (every row)" : " (most recent only)",
          }),
        ]),
      ]),
      node("div", { class: "row-pair" }, [
        node("dt", { text: "median size" }),
        node("dd", { class: "num", text: bytes(data.median_bytes) }),
      ]),
      node("div", { class: "row-pair" }, [
        node("dt", { text: "over " + bytes(data.threshold_bytes) }),
        node("dd", {}, [
          lamp(String(data.fat_count ?? 0), data.fat_count ? "attn" : "ok"),
        ]),
      ]),
    ]),
  );
  if ((data.largest || []).length) {
    wrap.append(
      dataTable(
        ["OBJECT", "DOCUMENT SIZE"],
        data.largest.map((row) => [
          node("td", { class: "num", text: "#" + row.id }),
          node("td", {
            class: row.bytes > data.threshold_bytes ? "num fail-text" : "num",
            text: bytes(row.bytes),
          }),
        ]),
      ),
    );
  }
  wrap.append(node("p", { class: "empty-hint", text: data.note || "" }));
  return wrap;
}

/* Presence.
 *
 * Two staff on the same flag queue is the normal case. Without this the second
 * person to open a record learns about the first when their write is refused
 * as a conflict, or does not learn at all. The heartbeat writes nothing to the
 * database, so it can run this often. */
let heartbeatTimer = null;

async function beat() {
  const result = await call("panels/views/actions/heartbeat/", {
    body: { panel: state.current || "", record: openRecord() },
  });
  if (!result.ok) return;
  const data = result.payload.result || {};
  state.presence = data.present || [];
  paintPresence(data.on_this_record || []);
  if (heartbeatTimer) clearTimeout(heartbeatTimer);
  heartbeatTimer = setTimeout(beat, (data.heartbeat_seconds || 30) * 1000);
}

/* What this operator has open, in the same form the panels use for a target,
 * so two people on one row match on the same string. */
function openRecord() {
  if (state.current === "records" && state.model && state.editing) {
    return state.model + "#" + state.editing;
  }
  if (state.current === "moderation" && state.modFlag) return "moderation.flag#" + state.modFlag;
  if (state.current === "audit" && state.auditOpen) return "console.audit#" + state.auditOpen;
  if (state.current === "jobs" && state.jobOpen) return "server.enginejob#" + state.jobOpen;
  return "";
}

function paintPresence(sharing) {
  let strip = document.getElementById("presence");
  if (!strip) {
    strip = node("div", { id: "presence" });
    el.strip = el.strip || el.lamps.parentElement;
    el.lamps.parentElement.append(strip);
  }
  strip.textContent = "";
  if (sharing.length) {
    strip.append(
      lamp(
        sharing.map((entry) => entry.actor_name).join(", ").toUpperCase() + " IS ON THIS RECORD",
        "attn",
      ),
    );
  } else if (state.presence.length) {
    strip.append(lamp(state.presence.length + " OTHER OPERATOR(S)", "off"));
  }
}

window.addEventListener("beforeunload", () => {
  navigator.sendBeacon?.(
    API + "panels/views/actions/depart/",
    new Blob(["{}"], { type: "application/json" }),
  );
});

async function drawViews() {
  const query = new URLSearchParams({ panel: state.viewPanel || "" });
  const result = await call("panels/views/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  const rows = data.rows || [];
  body.append(section("Saved views"));
  if (!rows.length) {
    body.append(
      empty(
        "NO SAVED VIEWS.",
        "Open a panel, set the filters you want, then select SAVE THIS VIEW.",
      ),
    );
  } else {
    body.append(
      dataTable(
        ["NAME", "PANEL", "DESCRIPTION", "SAVED BY", "PINNED", ""],
        rows.map((row) => [
          node("td", {}, [
            node("button", {
              type: "button",
              class: "linky",
              text: row.name,
              onclick: () => {
                location.hash = row.url.slice(1);
              },
            }),
          ]),
          cell(row.panel),
          cell(row.description),
          cell(row.created_by_name),
          node("td", {}, [row.pinned ? lamp("PINNED", "ok") : null]),
          node("td", {}, [
            node("button", {
              type: "button",
              text: "FORGET",
              onclick: async () => {
                const done = await call("panels/views/actions/forget/", {
                  body: { view_id: row.id },
                });
                if (report(done)) select_render();
              },
            }),
          ]),
        ]),
      ),
    );
  }
  body.append(node("p", { class: "empty-hint", text: data.note || "" }));

  body.append(section("Operators here now"));
  const present = data.present || [];
  body.append(
    present.length
      ? dataTable(
          ["OPERATOR", "PANEL", "RECORD"],
          present.map((entry) => [
            cell(entry.actor_name),
            cell(entry.panel),
            cell(entry.record),
          ]),
        )
      : empty("NOBODY ELSE HAS THE CONSOLE OPEN."),
  );
  body.append(node("p", { class: "empty-hint", text: data.presence_note || "" }));

  el.station.textContent = "";
  el.station.append(
    head("SAVED VIEWS", rows.length ? rows.length + " SAVED" : ""),
    node("div", { class: "toolbar" }, [
      picker("view-panel", "Panel", [...new Set(rows.map((row) => row.panel))], "viewPanel", "ALL PANELS"),
    ]),
    body,
  );
}

/* Save the current address as a named view. Offered on every panel, because
 * the thing worth saving is whatever the operator has just set up. */
function saveViewButton() {
  return node("button", {
    type: "button",
    text: "SAVE THIS VIEW",
    title: "Give this set of filters a name.",
    onclick: async () => {
      const name = prompt("Enter a name for this view.");
      if (!name) return;
      const description = prompt("Describe the view. Leave empty to skip.") || "";
      const query = (location.hash.split("?")[1] || "");
      const done = await call("panels/views/actions/save/", {
        body: { name, panel: state.current, query, description },
      });
      report(done);
    },
  });
}

const RENDERERS = {
  records: drawRecords,
  views: drawViews,
  jobs: drawJobs,
  eventbus: drawEventbus,
  database: drawDatabase,
  audit: drawAudit,
  objects: drawObjects,
  actions: drawActions,
  hooks: drawHooks,
  prototypes: drawPrototypes,
  repl: drawRepl,
  sql: drawSql,
  server: drawServer,
  sessions: drawSessions,
  moderation: drawModeration,
  authorization: drawAuthorization,
  errors: drawErrors,
  runtime: drawRuntime,
  logs: drawLogs,
  attributes: drawAttributes,
  migrations: drawMigrations,
  settings: drawSettings,
  health: drawHealth,
};

async function select_render() {
  writeUrl();
  const render = RENDERERS[state.current];
  if (!render) {
    el.station.textContent = "";
    el.station.append(
      head(String(state.current || "").toUpperCase()),
      empty("THIS STATION HAS NO VIEW IN THIS RELEASE."),
    );
    return;
  }
  await render();
}

function select(key) {
  if (state.current !== key && key === "records") {
    state.cursor = "";
    state.trail = [];
  }
  state.current = key;
  drawRail();
  select_render();
}

/* -------------------------------------------------------------------- feed */

/* One connection for the whole console. EventSource reconnects on its own and
 * replays what was missed through Last-Event-ID, so there is no retry loop to
 * write and no gap to paper over. */
const LIVE_LOG_LIMIT = 300;

function openFeed() {
  if (state.live.source) return;
  const source = new EventSource(API + "feed/", { withCredentials: true });
  state.live.source = source;

  source.addEventListener("health", (event) => {
    const payload = JSON.parse(event.data);
    state.live.health = payload;
    paintStripLamps(payload);
    if (state.current === "health") select_render();
  });

  source.addEventListener("metrics", (event) => {
    state.live.metrics = JSON.parse(event.data);
    if (state.current === "runtime") paintMetrics();
  });

  source.addEventListener("log", (event) => {
    state.live.log.push(JSON.parse(event.data));
    if (state.live.log.length > LIVE_LOG_LIMIT) {
      state.live.log.splice(0, state.live.log.length - LIVE_LOG_LIMIT);
    }
    if (state.current === "logs") appendLiveLog();
  });

  source.onerror = () => {
    /* EventSource retries by itself. Show the state rather than intervene. */
    const lamp = el.lamps.querySelector(".lamp[data-live]");
    if (lamp) lamp.dataset.state = "attn";
  };
}

/* The strip is the one place the feed is visible whichever station is open, so
 * an operator reading a table still sees the server change state. */
function paintStripLamps(payload) {
  for (const item of el.lamps.querySelectorAll(".lamp")) {
    const check = item.dataset.check;
    if (!check) continue;
    item.dataset.state = (payload.checks || {})[check] ? "ok" : "fail";
  }
  const live = el.lamps.querySelector(".lamp[data-live]");
  if (live) live.dataset.state = "ok";
  state.degraded = Boolean(payload.degraded);
}

function paintMetrics() {
  const host = document.getElementById("live-metrics");
  if (!host || !state.live.metrics) return;
  const payload = state.live.metrics;
  host.textContent = "";
  if (!payload.available) {
    host.append(node("p", { class: "empty-hint", text: payload.reason || "" }));
    return;
  }
  const table = node("table");
  table.append(
    node("thead", {}, [
      node("tr", {}, [
        node("th", { scope: "col", text: "METRIC" }),
        node("th", { scope: "col", text: "VALUE" }),
      ]),
    ]),
  );
  const tbody = node("tbody");
  for (const sample of (payload.samples || []).slice(0, 200)) {
    tbody.append(
      node("tr", {}, [cell(sample.name), node("td", { class: "num", text: String(sample.value) })]),
    );
  }
  table.append(tbody);
  host.append(table);
}

function appendLiveLog() {
  const host = document.getElementById("live-log");
  if (!host) return;
  host.textContent = "";
  for (const entry of state.live.log.slice(-LIVE_LOG_LIMIT)) {
    host.append(
      node("div", { class: "log-line" }, [
        node("span", { class: "log-source", text: entry.source }),
        node("span", { class: "log-text", text: entry.line }),
      ]),
    );
  }
  host.scrollTop = host.scrollHeight;
}

/* -------------------------------------------------------------------- boot */

async function boot() {
  const root = await call("");
  if (!root.ok) {
    el.station.textContent = "";
    el.station.append(
      head("CONSOLE"),
      empty(
        "YOU CANNOT USE THE CONSOLE.",
        root.status === 401 || root.status === 403
          ? "Sign in to the website. Use an account that holds console access."
          : root.payload.detail || "The console API did not answer.",
      ),
    );
    return;
  }
  state.panels = root.payload.panels || [];
  state.degraded = Boolean(root.payload.degraded);
  drawStrip(root.payload);
  const wanted = readUrl();
  const first = state.panels.find((panel) => panel.key === wanted) || state.panels[0];
  if (!first) {
    drawRail();
    el.station.textContent = "";
    el.station.append(head("CONSOLE"), empty("NO STATION IS AVAILABLE TO YOU."));
    return;
  }
  select(first.key);
  openFeed();
  beat();
}

/* The command palette.
 *
 * This replaces selecting a panel by its digit, which worked while there were
 * nine panels and reached half of them once there were eighteen. A palette
 * does not care how many there are, and it matches on the panel's description
 * as well as its name, so an operator who knows what they want but not what it
 * is called still arrives. */
function openPalette() {
  if (document.getElementById("palette")) return;

  const input = node("input", {
    type: "text",
    id: "palette-input",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "GO TO A STATION",
    "aria-label": "Go to a station",
  });
  const list = node("ul", { id: "palette-list", role: "listbox" });
  const overlay = node("div", { id: "palette", role: "dialog", "aria-modal": "true" }, [
    node("div", { class: "palette-box" }, [input, list]),
  ]);

  let matches = [];
  let cursor = 0;

  const paint = () => {
    const term = input.value.trim().toLowerCase();
    matches = state.panels.filter((panel) => {
      if (!term) return true;
      const hay = (panel.key + " " + panel.label + " " + (panel.description || "")).toLowerCase();
      return hay.includes(term);
    });
    cursor = Math.min(cursor, Math.max(0, matches.length - 1));
    list.textContent = "";
    matches.forEach((panel, index) => {
      list.append(
        node(
          "li",
          {
            role: "option",
            "aria-selected": index === cursor,
            class: index === cursor ? "current" : "",
            onclick: () => choose(index),
          },
          [
            node("span", { class: "palette-name", text: panel.label.toUpperCase() }),
            node("span", { class: "palette-hint", text: panel.description || "" }),
          ],
        ),
      );
    });
    if (!matches.length) {
      list.append(node("li", { class: "palette-empty", text: "NO STATION MATCHES." }));
    }
  };

  const close = () => overlay.remove();

  const choose = (index) => {
    const panel = matches[index];
    if (!panel) return;
    close();
    select(panel.key);
  };

  input.addEventListener("input", paint);
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || (event.key === "n" && event.ctrlKey)) {
      event.preventDefault();
      cursor = Math.min(cursor + 1, matches.length - 1);
      paint();
    } else if (event.key === "ArrowUp" || (event.key === "p" && event.ctrlKey)) {
      event.preventDefault();
      cursor = Math.max(cursor - 1, 0);
      paint();
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(cursor);
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  });
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });

  document.body.append(overlay);
  paint();
  input.focus();
}

/* Keyboard first: the audience already works this way, and the surface is
 * dense enough that reaching for a mouse costs more than it saves. */
document.addEventListener("keydown", (event) => {
  // The palette opens from anywhere, including from inside a field, because
  // an operator halfway through typing a filter is exactly who wants it.
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openPalette();
    return;
  }
  if (event.target.matches("input, select, textarea")) return;
  if (event.key === "/") {
    const search = document.querySelector('input[type="search"]');
    if (search) {
      event.preventDefault();
      search.focus();
    }
    return;
  }
  if (event.key === "Escape") el.notice.hidden = true;
});

window.addEventListener("hashchange", () => {
  const wanted = readUrl();
  if (wanted && wanted !== state.current) {
    select(wanted);
  } else {
    select_render();
  }
});

boot();
