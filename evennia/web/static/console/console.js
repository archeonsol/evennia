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
  page: 1,
  search: "",
  order: "",
  attrModel: "",
  attrSearch: "",
};

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
  el.lamps.append(lamp("DATABASE", "ok"));
  el.lamps.append(lamp("GAME SERVER", root.degraded ? "attn" : "ok"));
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
  const panel = node("div", { class: "station" });
  const models = await call("panels/records/actions/models/", { body: {} });
  if (!report(models)) {
    el.station.textContent = "";
    el.station.append(head("RECORDS"), empty("THE MODEL LIST IS NOT AVAILABLE."));
    return;
  }
  const list = models.payload.result || [];
  if (!state.model && list.length) state.model = list[0].label;

  const select = node("select", {
    id: "model-select",
    onchange: (event) => {
      state.model = event.target.value;
      state.page = 1;
      state.order = "";
      select_render();
    },
  });
  for (const item of list) {
    select.append(
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
    placeholder: "SEARCH TEXT COLUMNS",
    "aria-label": "Search text columns",
    onchange: (event) => {
      state.search = event.target.value;
      state.page = 1;
      select_render();
    },
  });

  const query = new URLSearchParams({
    model: state.model,
    page: String(state.page),
    search: state.search,
    order: state.order,
  });
  const rows = await call(`panels/records/rows/?${query}`);
  const data = rows.payload.rows || {};
  const body = node("div", { class: "panel-body" });

  if (!rows.ok) {
    report(rows);
    body.append(empty("THE ROWS ARE NOT AVAILABLE.", rows.payload.detail || ""));
  } else if (!data.rows || data.rows.length === 0) {
    body.append(
      empty(
        "NO ROWS.",
        state.search
          ? "No row matches the search text. Clear the search to show all rows."
          : "This model has no rows.",
      ),
    );
  } else {
    const table = node("table");
    const headRow = node("tr");
    for (const field of data.fields) {
      const next = state.order === field ? `-${field}` : field;
      headRow.append(
        node("th", { scope: "col" }, [
          node("button", {
            type: "button",
            text: field + (state.order === field ? " ↑" : state.order === `-${field}` ? " ↓" : ""),
            onclick: () => {
              state.order = next;
              state.page = 1;
              select_render();
            },
          }),
        ]),
      );
    }
    table.append(node("thead", {}, [headRow]));
    const tbody = node("tbody");
    for (const row of data.rows) {
      tbody.append(node("tr", {}, data.fields.map((field) => cell(row[field]))));
    }
    table.append(tbody);
    body.append(table);
  }

  const total = data.total || 0;
  const size = data.page_size || 50;
  const pages = Math.max(1, Math.ceil(total / size));
  const policy = data.writable
    ? lamp("WRITE ENABLED", "ok")
    : lamp("DOMAIN OWNED", "attn");

  panel.append(
    head("RECORDS", total ? `${total} ROWS` : ""),
    node("div", { class: "toolbar" }, [
      node("div", { class: "field" }, [
        node("label", { class: "legend", for: "model-select", text: "Model" }),
        select,
      ]),
      node("div", { class: "field" }, [search]),
      node("span", { class: "spacer" }),
      policy,
      node("button", {
        type: "button",
        text: "PREVIOUS",
        disabled: state.page <= 1,
        onclick: () => { state.page -= 1; select_render(); },
      }),
      node("span", { class: "legend", text: `PAGE ${state.page} OF ${pages}` }),
      node("button", {
        type: "button",
        text: "NEXT",
        disabled: state.page >= pages,
        onclick: () => { state.page += 1; select_render(); },
      }),
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

  el.station.textContent = "";
  while (panel.firstChild) el.station.append(panel.firstChild);
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

const RENDERERS = {
  records: drawRecords,
  attributes: drawAttributes,
  migrations: drawMigrations,
  settings: drawSettings,
  health: drawHealth,
};

async function select_render() {
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
  state.current = key;
  if (key === "records") {
    state.page = 1;
  }
  drawRail();
  history.replaceState(null, "", `#${key}`);
  select_render();
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
  const wanted = location.hash.replace("#", "");
  const first = state.panels.find((panel) => panel.key === wanted) || state.panels[0];
  if (!first) {
    drawRail();
    el.station.textContent = "";
    el.station.append(head("CONSOLE"), empty("NO STATION IS AVAILABLE TO YOU."));
    return;
  }
  select(first.key);
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

boot();
