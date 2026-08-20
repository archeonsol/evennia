/* Engine console client.
 *
 * Plain ES modules, no build step and no dependencies. That is a deliberate
 * choice rather than a shortcut: the console currently renders a station rail
 * and four panels, the engine promises that a game never runs npm, and a
 * committed build artifact rots. Revisit when the live feed arrives or the
 * panel count passes roughly eight, whichever comes first.
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

const RENDERERS = {
  records: drawRecords,
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
