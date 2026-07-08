// Render an R1 RenderNode payload to HTML for the log.
//
// A node payload carries `html` (server-parsed, coloured), `body` (raw markup
// fallback), and `refs` - [{ name, char_id }] resolved for THIS viewer. We render
// the html and wrap each ref's resolved name in a `.char-ref` span carrying its
// stable `char_id`. These are inert **identity anchors** (no click behaviour) -
// they exist so the shell can later apply per-character identity highlighting and
// screen-reader accessibility. The wrapping only touches text between tags, so it
// nests safely inside colour spans.

interface CharRef {
  name?: string;
  char_id?: number | string;
}
interface NodePayload {
  html?: string;
  body?: string;
  refs?: CharRef[];
  msgType?: string;
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

// Wrap occurrences of `name` in the *text* portions of `html` (never inside a
// tag) with a clickable char-ref span.
function wrapName(html: string, name: string, id: number | string | undefined): string {
  const re = new RegExp(escapeRe(name), "g");
  const open = `<span class="char-ref" data-char-id="${id ?? ""}" data-name="${escapeAttr(name)}">`;
  return html.replace(/(<[^>]+>)|([^<]+)/g, (_m, tag, text) => {
    if (tag) return tag;
    return text.replace(re, `${open}${name}</span>`);
  });
}

export function renderNodeHtml(node: NodePayload): string {
  let html = String(node.html ?? node.body ?? "");
  // Longest names first so a longer sdesc isn't clobbered by a shorter substring.
  const refs = [...(node.refs ?? [])]
    .filter((r) => r && r.name)
    .sort((a, b) => (b.name!.length ?? 0) - (a.name!.length ?? 0));
  for (const ref of refs) {
    html = wrapName(html, ref.name!, ref.char_id);
  }
  return html;
}
