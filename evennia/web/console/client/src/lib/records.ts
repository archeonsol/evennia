/* The two Records operations that leave the console.
 *
 * Both are here rather than in the component because both are consequential in
 * a way rendering is not: one takes data off the server, and the other removes
 * rows from it. Testing either through a rendered table would mean testing the
 * table.
 */

import { call } from "./api";
import { report } from "./report";
import { view, local } from "./state.svelte";
import { askText } from "./dialog.svelte";

interface ExportPayload {
  body?: string;
  content_type?: string;
  filename?: string;
}

/**
 * Download the current listing.
 *
 * The server records what was taken and by whom *before* it answers, so the
 * download and the record cannot disagree. That ordering is the server's, and
 * nothing here may work around it.
 */
export async function runExport(format: "csv" | "json"): Promise<boolean> {
  let filters: { field: string; lookup: string; value: string }[] = [];
  try {
    const parsed = JSON.parse(view.recordFilters || "[]");
    if (Array.isArray(parsed)) filters = parsed;
  } catch {
    filters = [];
  }
  const query = new URLSearchParams({
    model: view.model,
    search: view.search,
    order: view.order,
    columns: view.columns,
  });
  for (const item of filters) {
    if (!item?.field || !item.lookup || (item.lookup !== "isnull" && !String(item.value || "").trim())) continue;
    query.set(
      `f.${item.field}__${item.lookup}`,
      item.lookup === "isnull" ? String(item.value || "true") : String(item.value),
    );
  }
  const result = await call<{ result?: ExportPayload }>(
    `panels/records/actions/export/?${query}`,
    {
      body: {
        model: view.model,
        fmt: format,
        search: view.search,
        order: view.order,
        columns: view.columns,
      },
    },
  );
  if (!report(result)) return false;

  const data = result.payload.result || {};
  const blob = new Blob([data.body || ""], { type: data.content_type || "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = data.filename || "export.txt";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return true;
}

interface PreviewPayload {
  rows?: { id: string | number; reach: number }[];
  missing?: unknown[];
  found?: number;
  total_cascade?: number;
  model?: string;
}

/**
 * Count first, decide second, then delete.
 *
 * Django admin commits and reports afterwards. This reports and then asks,
 * because a cascade is the part an operator cannot see from the table and
 * cannot undo once it has run.
 *
 * Returns:
 *   Whether rows were deleted, so the caller knows to reload.
 */
export async function previewDelete(ids: string[]): Promise<boolean> {
  const result = await call<{ result?: PreviewPayload }>(
    "panels/records/actions/preview_delete/",
    { body: { model: view.model, ids } },
  );
  if (!report(result)) return false;
  const data = result.payload.result || {};

  const lines = (data.rows || [])
    .slice(0, 12)
    .map((row) => `  #${row.id} removes ${row.reach} related row(s)`);
  const missing = (data.missing || []).length
    ? `\n${(data.missing || []).length} selected row(s) no longer exist.`
    : "";

  const reason = await askText({
    title: `Delete ${data.found || 0} row(s)`,
    description:
      `Model: ${data.model}\n` +
      `Related rows removed: ${data.total_cascade || 0}\n` +
      `${lines.join("\n")}${missing}\n\nThis cannot be undone. Review the cascade, then enter the reason.`,
    label: "Deletion reason",
    input: "textarea",
    confirmLabel: "DELETE ROWS",
    danger: true,
  });
  if (!reason) return false;

  const done = await call<{ result?: { deleted?: number[]; vetoed?: number[]; failed?: number[] } }>("panels/records/actions/delete/", {
    body: { model: view.model, ids, reason },
  });
  if (!report(done)) {
    if (done.outcome === "indeterminate" && done.payload.result) {
      local.chosen = [];
      return true;
    }
    return false;
  }
  local.chosen = [];
  return true;
}
