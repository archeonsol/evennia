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
  live: { source: null, health: null, metrics: null, log: [] },
};

/* Keys carried in the address bar. A view an operator reached by clicking must
 * be reachable again by pasting, or a bug report cannot contain the thing it
 * is about. `trail` stays out: it is how you walked here, not where you are. */
const URL_KEYS = ["model", "cursor", "columns", "search", "order", "attrModel", "attrSearch", "editing"];

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
  el.rail.append(node("div", { class: "rail-heading" }, [node("span", { class: "legend", text: "Stations" })]));
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
      const id = row[data.columns[0]];
      tbody.append(
        node("tr", {
          title: data.writable ? "Open this row" : "",
          onclick: () => {
            if (!data.writable) return;
            state.editing = String(id);
            select_render();
          },
        }, data.columns.map((field) => cell(row[field]))),
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
      share,
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
          cell(row.key),
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

  body.append(section("Alarms"), alarms, section("Caches"), caches, section("Metrics"));
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
async function drawModeration() {
  const query = new URLSearchParams({ state: state.modState || "" });
  const result = await call("panels/moderation/rows/?" + query);
  report(result);
  const data = result.payload.rows || {};
  const body = node("div", { class: "panel-body" });

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
              flag_id: flag.id,
            },
          });
          if (report(done)) {
            state.modFlag = null;
            select_render();
          }
        },
      }),
    ]),
  );
  return wrap;
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
      text: "Set " + data.setting + " = True in the game's settings to permit it here.",
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
            title: "Recorded permanently, and in the watched account's own timeline",
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

const RENDERERS = {
  records: drawRecords,
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
}

/* Keyboard first: the audience already works this way, and the surface is
 * dense enough that reaching for a mouse costs more than it saves. */
document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, select, textarea")) return;
  const index = Number(event.key) - 1;
  if (index >= 0 && index < state.panels.length) {
    select(state.panels[index].key);
    return;
  }
  if (event.key === "/") {
    const search = document.querySelector('input[type="search"]');
    if (search) {
      event.preventDefault();
      search.focus();
    }
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
