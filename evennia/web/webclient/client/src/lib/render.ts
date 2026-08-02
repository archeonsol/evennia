import { pipeToHtml } from "./markup";

// Render an R1 RenderNode payload to HTML for the log.
//
// A render.v1 payload contains a parity HTML/body plus optional semantic blocks
// and viewer-scoped opaque entity handles. Handles rotate when perception changes
// and never expose a database id.

interface CharRef {
  name?: string;
  label?: string;
  handle?: string;
  kind?: string;
  recognized?: boolean;
  affordances?: string[];
}
interface NodePayload {
  schema?: string;
  node_id?: string;
  correlation_id?: string;
  html?: string;
  body?: string;
  refs?: CharRef[];
  msg_type?: string;
  blocks?: RenderBlock[];
}

interface RenderBlock {
  type?: "line" | "paragraph" | "section" | "list" | "system";
  text?: string;
  title?: string;
  key?: string;
  style?: string;
  level?: string;
  ordered?: boolean;
  items?: string[];
  children?: RenderBlock[];
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

// Wrap occurrences of `name` in the *text* portions of `html` (never inside a
// tag) with a clickable char-ref span.
function wrapName(html: string, name: string, handle: string | undefined): string {
  const re = new RegExp(escapeRe(name), "g");
  const open = `<span class="entity-ref" data-entity-handle="${escapeAttr(handle ?? "")}" data-name="${escapeAttr(name)}">`;
  return html.replace(/(<[^>]+>)|([^<]+)/g, (_m, tag, text) => {
    if (tag) return tag;
    return text.replace(re, `${open}${name}</span>`);
  });
}

// Node bodies and block text carry Evennia markup, so they go through
// pipeToHtml (which escapes as it parses) rather than being escaped flat —
// escaping alone would put literal |r codes in front of the player.

function blockHtml(block: RenderBlock): string {
  const cls = escapeAttr(block.style ?? "");
  if (block.type === "section") {
    const title = block.title ? `<h3>${pipeToHtml(block.title)}</h3>` : "";
    return `<section data-key="${escapeAttr(block.key ?? "")}" class="${cls}">${title}${(block.children ?? []).map(blockHtml).join("")}</section>`;
  }
  if (block.type === "list") {
    const tag = block.ordered ? "ol" : "ul";
    return `<${tag} class="${cls}">${(block.items ?? []).map((item) => `<li>${pipeToHtml(item)}</li>`).join("")}</${tag}>`;
  }
  if (block.type === "paragraph") return `<p class="${cls}">${pipeToHtml(block.text ?? "")}</p>`;
  if (block.type === "system") return `<div class="system ${escapeAttr(block.level ?? "info")} ${cls}">${pipeToHtml(block.text ?? "")}</div>`;
  return `<div class="line ${cls}">${pipeToHtml(block.text ?? "")}</div>`;
}

export function renderNodeHtml(node: NodePayload): string {
  // Server HTML is the byte-parity/color anchor while surfaces migrate. A node
  // without it can still render natively from semantic blocks.
  let html = String(node.html ?? ((node.blocks?.length ?? 0) > 0 ? node.blocks!.map(blockHtml).join("") : pipeToHtml(node.body ?? "")));
  // Longest names first so a longer sdesc isn't clobbered by a shorter substring.
  const refs = [...(node.refs ?? [])]
    .filter((r) => r && (r.label || r.name))
    .sort((a, b) => ((b.label ?? b.name)!.length ?? 0) - ((a.label ?? a.name)!.length ?? 0));
  for (const ref of refs) {
    const label = ref.label ?? ref.name!;
    html = wrapName(html, label, ref.handle);
  }
  return html;
}
