// Scrollback export.
//
// The log is kept as rendered HTML per line (`LogLine.html`), which is where
// every colour, background, underline and blink in the buffer lives. The plain
// `.txt` download throws all of that away, so the same buffer also exports as:
//
//   - `.ans`  — the styling put back as SGR escapes, which is what another MUD
//               client or a terminal `cat` expects. Colours ride as their
//               xterm-256 indices, the same numbers the server sent.
//   - `.html` — a standalone page that looks like the client did, with the
//               palette resolved out of the live document so a game's theme
//               override travels with the file.
//
// Both walk the DOM rather than the HTML string: the markup parser emits one
// span per style run but MXP links nest inside and across them, so effective
// style is an ancestor question, not a token-order one.

import type { LogLine } from "./session.svelte";

export type TranscriptFormat = "txt" | "ansi" | "html";

export interface TranscriptOptions {
  /** Prefix each line with its HH:MM:SS, matching the log's own gutter. */
  timestamps?: boolean;
  /** Document title and <h1> for the HTML export. */
  title?: string;
}

export interface TranscriptFile {
  body: string;
  mime: string;
  ext: string;
}

const ESC = "\u001b[";
const RESET = ESC + "0m";

//: Elements that must not survive into a saved page. In the client this HTML is
//: inserted with innerHTML, where a <script> never runs; opened from disk as a
//: file it would. Same bytes, different privilege — so the export strips what
//: the client only got away with by accident, plus the remote-loading embeds a
//: transcript has no business fetching.
const DROP_TAGS = new Set([
  "SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT", "IFRAME", "FRAME", "OBJECT",
  "EMBED", "APPLET", "LINK", "META", "BASE", "FORM", "INPUT", "BUTTON",
]);

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function hhmmss(ts: number): string {
  const d = new Date(ts);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function parse(html: string): DocumentFragment {
  const template = document.createElement("template");
  template.innerHTML = html;
  return template.content;
}

/** The xterm index a `color-NNN` / `bgcolor-NNN` class carries, if any. */
function paletteIndex(cls: string, prefix: string): number | null {
  if (!cls.startsWith(prefix)) return null;
  const n = Number(cls.slice(prefix.length));
  return Number.isInteger(n) && n >= 0 && n <= 255 ? n : null;
}

/** `#rrggbb` or `rgb(r, g, b)` → [r, g, b]. */
function rgb(value: string): [number, number, number] | null {
  const v = value.trim();
  const hex = /^#([0-9a-f]{6})$/i.exec(v);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const fn = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(v);
  return fn ? [Number(fn[1]), Number(fn[2]), Number(fn[3])] : null;
}

interface Style {
  underline: boolean;
  blink: boolean;
  fg: number | null;
  bg: number | null;
  /** Truecolor overrides, which the parser emits as inline styles. */
  fgHex: string | null;
  bgHex: string | null;
}

const BARE: Style = {
  underline: false, blink: false, fg: null, bg: null, fgHex: null, bgHex: null,
};

/**
 * Fold one element's own styling onto the run inherited from its ancestors.
 *
 * Nothing here resets: a nested element only ever adds, which matches how the
 * parser builds spans and means a link inside a coloured run stays coloured.
 */
function extend(base: Style, el: Element): Style {
  const out = { ...base };
  for (const cls of el.classList) {
    if (cls === "underline") out.underline = true;
    else if (cls === "blink") out.blink = true;
    else {
      const bg = paletteIndex(cls, "bgcolor-");
      if (bg !== null) {
        out.bg = bg;
        continue;
      }
      const fg = paletteIndex(cls, "color-");
      if (fg !== null) out.fg = fg;
    }
  }
  const inline = (el as HTMLElement).style;
  if (inline) {
    if (inline.color) out.fgHex = inline.color;
    if (inline.backgroundColor) out.bgHex = inline.backgroundColor;
  }
  return out;
}

/** The SGR sequence for a style run, or "" for an unstyled one. */
function sgr(s: Style): string {
  const codes: string[] = [];
  if (s.underline) codes.push("4");
  if (s.blink) codes.push("5");
  // Truecolor wins over the palette class, exactly as the inline style does in
  // the browser. A terminal without 24-bit support degrades on its own.
  const bgTrue = s.bgHex ? rgb(s.bgHex) : null;
  if (bgTrue) codes.push(`48;2;${bgTrue[0]};${bgTrue[1]};${bgTrue[2]}`);
  else if (s.bg !== null) codes.push(`48;5;${s.bg}`);
  const fgTrue = s.fgHex ? rgb(s.fgHex) : null;
  if (fgTrue) codes.push(`38;2;${fgTrue[0]};${fgTrue[1]};${fgTrue[2]}`);
  else if (s.fg !== null) codes.push(`38;5;${s.fg}`);
  return codes.length ? ESC + codes.join(";") + "m" : "";
}

/** One log line's HTML as SGR-coded text. */
export function htmlToAnsi(html: string): string {
  let out = "";
  let active = "";

  const emit = (text: string, style: Style) => {
    if (!text) return;
    const want = sgr(style);
    if (want !== active) {
      // SGR attributes accumulate, so changing a run means clearing the old one
      // and restating it rather than adding to it. Nothing to clear when no run
      // is open, which keeps bare text free of a pointless leading reset.
      if (active) out += RESET;
      out += want;
      active = want;
    }
    out += text;
  };

  const visit = (node: Node, style: Style) => {
    if (node.nodeType === Node.TEXT_NODE) {
      emit(node.textContent ?? "", style);
      return;
    }
    if (!(node instanceof Element)) return;
    if (DROP_TAGS.has(node.tagName) || node.hasAttribute("hidden")) return;
    if (node.tagName === "BR") {
      out += "\n";
      return;
    }
    if (node.tagName === "IMG") {
      const alt = node.getAttribute("alt") || node.getAttribute("src") || "";
      if (alt) emit(`[${alt}]`, style);
      return;
    }
    const next = extend(style, node);
    node.childNodes.forEach((child) => visit(child, next));
  };

  parse(html).childNodes.forEach((node) => visit(node, BARE));
  return active ? out + RESET : out;
}

/** Strip a fragment down to what is safe to write into a saved page. */
function sanitize(node: Element | DocumentFragment): void {
  for (const el of Array.from(node.querySelectorAll("*"))) {
    if (DROP_TAGS.has(el.tagName)) {
      el.remove();
      continue;
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim().toLowerCase();
      const isUrl = name === "href" || name === "src" || name === "xlink:href";
      if (name.startsWith("on") || (isUrl && value.startsWith("javascript:"))) {
        el.removeAttribute(attr.name);
      }
    }
  }
}

function escapeText(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

//: The standard xterm-256 ramp: 0-15 base ANSI, 16-231 a 6x6x6 cube, 232-255
//: greyscale. Same arithmetic as scripts/gen-palette.mjs, which generates the
//: stylesheet - kept here as the value to fall back on when the document cannot
//: answer for a class.
const BASE16 = [
  "#000000", "#800000", "#008000", "#808000", "#000080", "#800080", "#008080", "#c0c0c0",
  "#808080", "#ff0000", "#00ff00", "#ffff00", "#0000ff", "#ff00ff", "#00ffff", "#ffffff",
];
const CUBE = [0, 95, 135, 175, 215, 255];

export function xtermHex(i: number): string {
  if (i < 16) return BASE16[i];
  const hex = (n: number) => n.toString(16).padStart(2, "0");
  if (i < 232) {
    const n = i - 16;
    return `#${hex(CUBE[Math.floor(n / 36) % 6])}${hex(CUBE[Math.floor(n / 6) % 6])}${hex(CUBE[n % 6])}`;
  }
  const v = 8 + (i - 232) * 10;
  return `#${hex(v)}${hex(v)}${hex(v)}`;
}

/**
 * Resolve the palette classes a buffer actually uses against the live document.
 *
 * Reading them back rather than shipping a copy of ansi-palette.css is what
 * makes a game's own overrides (loaded after the shell's neutral base) come out
 * in the file, and it keeps the export to the handful of colours in play
 * instead of all 512 rules.
 *
 * A class the document has no rule for computes to whatever the probe
 * inherits - the body colour - which would flatten the whole transcript to one
 * shade without erroring. So each class is measured against an unclassed probe
 * and only believed when it actually changes something; otherwise the standard
 * xterm value stands in.
 */
function paletteCss(classes: Set<string>): string {
  const probe = document.createElement("span");
  const bare = document.createElement("span");
  for (const el of [probe, bare]) {
    el.style.position = "absolute";
    el.style.visibility = "hidden";
    document.body.appendChild(el);
  }
  const base = getComputedStyle(bare);
  const baseFg = base.color;
  const baseBg = base.backgroundColor;
  const rules: string[] = [];
  try {
    for (const cls of Array.from(classes).sort()) {
      probe.className = cls;
      const computed = getComputedStyle(probe);
      const bg = paletteIndex(cls, "bgcolor-");
      if (bg !== null) {
        const value =
          computed.backgroundColor !== baseBg ? computed.backgroundColor : xtermHex(bg);
        rules.push(`.${cls}{background-color:${value}}`);
        continue;
      }
      const fg = paletteIndex(cls, "color-");
      if (fg !== null) {
        const value = computed.color !== baseFg ? computed.color : xtermHex(fg);
        rules.push(`.${cls}{color:${value}}`);
      }
    }
  } finally {
    probe.remove();
    bare.remove();
  }
  return rules.join("\n");
}

function themeVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Plain text, one line per log line. */
function toText(lines: LogLine[], opts: TranscriptOptions): string {
  return lines
    .map((l) => (opts.timestamps ? `${hhmmss(l.ts)} ${l.text}` : l.text))
    .join("\n");
}

function toAnsi(lines: LogLine[], opts: TranscriptOptions): string {
  return lines
    .map((l) => {
      const body = htmlToAnsi(l.html);
      // The gutter is deliberately unstyled: it is the client's addition, not
      // the game's output, and it must not colour the line that follows.
      return opts.timestamps ? `${hhmmss(l.ts)} ${body}` : body;
    })
    .join("\n");
}

function toHtml(lines: LogLine[], opts: TranscriptOptions): string {
  const classes = new Set<string>();
  const bodies = lines.map((l) => {
    const frag = parse(l.html);
    sanitize(frag);
    for (const el of Array.from(frag.querySelectorAll("[class]"))) {
      for (const cls of el.classList) classes.add(cls);
    }
    const holder = document.createElement("div");
    holder.appendChild(frag);
    return holder.innerHTML;
  });

  const title = opts.title || "Transcript";
  const rows = bodies
    .map((body, i) => {
      const stamp = opts.timestamps
        ? `<span class="ts">${hhmmss(lines[i].ts)}</span>`
        : "";
      return `<div class="line" data-cat="${escapeText(lines[i].cat)}">${stamp}${body}</div>`;
    })
    .join("\n");

  // System mono, not the player's chosen font: the file has no webfonts and no
  // "Shell Glyphs" fallback with it, and a stack whose box-drawing characters
  // come from somewhere at the wrong advance tears every table in the
  // transcript. The local monos all carry U+2500-25FF at their own cell width.
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeText(title)}</title>
<style>
:root { color-scheme: dark; }
body {
  margin: 0; padding: 1rem;
  background: ${themeVar("--bg", "#0a0806")};
  color: ${themeVar("--fg", "#cdbfa6")};
  font-family: ui-monospace, "Cascadia Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace;
  font-size: 15px; line-height: 1.5;
}
.line { white-space: pre-wrap; word-break: break-word; }
.ts { color: ${themeVar("--fg-faint", "#4c4338")}; margin-right: 0.8ch; user-select: none; }
a { color: inherit; }
img { max-width: 100%; }
.underline { text-decoration: underline; }
.blink { animation: ev-blink 1s step-end infinite; }
@keyframes ev-blink { 50% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .blink { animation: none; } }
${paletteCss(classes)}
</style>
</head>
<body>
${rows}
</body>
</html>
`;
}

/** Render the buffer in one of the download formats. */
export function buildTranscript(
  lines: LogLine[],
  format: TranscriptFormat,
  opts: TranscriptOptions = {},
): TranscriptFile {
  if (format === "ansi") {
    return { body: toAnsi(lines, opts), mime: "text/plain;charset=utf-8", ext: "ans" };
  }
  if (format === "html") {
    return { body: toHtml(lines, opts), mime: "text/html;charset=utf-8", ext: "html" };
  }
  return { body: toText(lines, opts), mime: "text/plain;charset=utf-8", ext: "txt" };
}
