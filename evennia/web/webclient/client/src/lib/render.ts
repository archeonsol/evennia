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

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
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
  return String(node.html ?? ((node.blocks?.length ?? 0) > 0 ? node.blocks!.map(blockHtml).join("") : pipeToHtml(node.body ?? "")));
}
