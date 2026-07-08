// Client-side Evennia pipe-code → HTML fallback when the server omits html fields.
// Primary path: server sends pre-parsed html/sender_html (parse_html). This mirrors
// legacy custom-client.js ansiToHtml for resilience.

const PIPE =
  /^(\|\[?[0-5][0-5][0-5])|(\|\[[0-9][0-9]?m)|(\|\[?=[a-z])|(\|[![]?[unrgybmcwxhRGYBMCWXH_/>*^-])/;

const CSS: Record<string, string> = {
  "|n": "normal",
  "|r": "color-009",
  "|g": "color-010",
  "|y": "color-011",
  "|b": "color-012",
  "|m": "color-013",
  "|c": "color-014",
  "|w": "color-015",
  "|x": "color-008",
  "|R": "color-001",
  "|G": "color-002",
  "|Y": "color-003",
  "|B": "color-004",
  "|M": "color-005",
  "|C": "color-006",
  "|W": "color-007",
  "|X": "color-000",
  "|[r": "bgcolor-196",
  "|[g": "bgcolor-046",
  "|[y": "bgcolor-226",
  "|[b": "bgcolor-021",
  "|[m": "bgcolor-201",
  "|[c": "bgcolor-051",
  "|[w": "bgcolor-231",
  "|[x": "bgcolor-102",
  "|[R": "bgcolor-001",
  "|[G": "bgcolor-002",
  "|[Y": "bgcolor-003",
  "|[B": "bgcolor-004",
  "|[M": "bgcolor-005",
  "|[C": "bgcolor-006",
  "|[W": "bgcolor-007",
  "|[X": "bgcolor-000",
  "|u": "underline",
  "|[0m": "normal",
  "|[1m": "normal",
  "|[22m": "normal",
  "|[36m": "color-006",
  "|[37m": "color-015",
};

function zfill(s: string, n: number): string {
  while (s.length < n) s = "0" + s;
  return s;
}

function esc(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pipe2css(code: string): string {
  const m1 = code.match(/^(\|\[?[nrgybmcwxhRGYBMCWX])/);
  if (m1) return CSS[m1[1]] ?? "color-102";
  const m2 = code.match(/^(\|\[?=[a-z])/);
  if (m2) return CSS[m2[1]] ?? "color-102";
  const m3 = code.match(/^(\|\[[0-9][0-9]?m)/);
  if (m3) return CSS[m3[1]] ?? "color-102";
  if (/^\|n/.test(code)) return "normal";
  if (/^\|u/.test(code)) return "underline";
  const fg = code.match(/^\|([0-5][0-5][0-5])/);
  if (fg) return "color-" + zfill(String(parseInt(fg[1], 6) + 16), 3);
  const bg = code.match(/^\|\[([0-5][0-5][0-5])/);
  if (bg) return "bgcolor-" + zfill(String(parseInt(bg[1], 6) + 16), 3);
  return "color-102";
}

/** True when the string already looks like server-parsed HTML. */
export function looksLikeHtml(text: string): boolean {
  return /<\/?(?:span|div|p|br)\b/i.test(text);
}

/** Parse Evennia pipe markup into class-based HTML (ansi-palette.css). */
export function pipeToHtml(text: string): string {
  if (!text) return "";
  if (looksLikeHtml(text)) return text;
  if (!text.includes("|")) return esc(text);

  let fg = "color-102";
  let bg = "";
  let underline = false;
  let html = "";

  const wrap = (inner: string) => {
    if (!inner) return "";
    const classes = [fg, bg, underline ? "underline" : ""].filter(Boolean).join(" ");
    return classes ? `<span class="${classes}">${esc(inner)}</span>` : esc(inner);
  };

  const parts = text.split(/(\n|\|[/])/);
  for (const chunk of parts) {
    if (chunk === "\n" || chunk === "") {
      if (chunk === "\n") html += "<br>";
      continue;
    }
    const segs = chunk.replace(/\|\|/g, "\u0001").split("|");
    const lead = segs[0].replace(/\u0001/g, "|");
    if (lead) html += wrap(lead);

    for (let i = 1; i < segs.length; i++) {
      const raw = "|" + segs[i].replace(/\u0001/g, "|");
      const tag = raw.match(PIPE);
      const code = tag ? tag[0] : "";
      const rest = tag ? raw.slice(code.length) : raw;
      const css = code ? pipe2css(code) : "";
      if (css === "normal") {
        fg = "color-102";
        bg = "";
        underline = false;
      } else if (css === "underline") {
        underline = true;
      } else if (css.startsWith("color-")) {
        fg = css;
      } else if (css.startsWith("bgcolor-")) {
        bg = css;
      }
      html += wrap(rest);
    }
  }
  return html;
}

/** Render a message body: prefer server html, else client parse. */
export function renderBody(html?: string, text?: string): string {
  if (html) return html;
  return pipeToHtml(text ?? "");
}

/** Render a sender label: prefer server sender_html, else client parse. */
export function renderSender(senderHtml?: string, sender?: string): string {
  if (senderHtml) return senderHtml;
  return pipeToHtml(sender ?? "");
}
