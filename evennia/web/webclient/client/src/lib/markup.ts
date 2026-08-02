// Client-side Evennia pipe-code → HTML.
//
// The server normally ships pre-parsed `html` alongside each node body, but it
// will omit it for a client declaring `caps.rendersMarkup` — and several panels
// already fall back here whenever `html` is absent. Either way the output must
// match `evennia.utils.text2html.parse_html` exactly, so this mirrors that
// parser's rules rather than approximating them:
//
//   - a style run becomes one <span class="…">, opened lazily so a code with no
//     text after it emits nothing
//   - `|n` closes an open run; text after it is bare
//   - class order is `underline`, then background, then foreground
//   - `color-007` (default fg) and `bgcolor-000` (default bg) are omitted, which
//     is why `|W` and `|[X` produce `<span class="">`
//   - `"` is not escaped in text content
//
// `src/lib/markup.test.ts` asserts this against a fixture generated from the
// server parser (`scripts/gen-markup-parity.py`). Regenerate it if the server
// parser changes; a diff there means the shell would render something else.

/** Named foreground codes → xterm index. */
const FG_INDEX: Record<string, number> = {
  r: 9, g: 10, y: 11, b: 12, m: 13, c: 14, w: 15, x: 8,
  R: 1, G: 2, Y: 3, B: 4, M: 5, C: 6, W: 7, X: 0,
};

/**
 * Named background codes → xterm index.
 *
 * Not the foreground table: the bright backgrounds map onto the vivid 256-colour
 * entries rather than the 8-15 block, so `|[r` is 196 where `|r` is 9.
 */
const BG_INDEX: Record<string, number> = {
  r: 196, g: 46, y: 226, b: 21, m: 201, c: 51, w: 231, x: 102,
  R: 1, G: 2, Y: 3, B: 4, M: 5, C: 6, W: 7, X: 0,
};

/** The indices the server treats as defaults and leaves off the class list. */
const DEFAULT_FG = 7;
const DEFAULT_BG = 0;

function zfill(s: string, n: number): string {
  while (s.length < n) s = "0" + s;
  return s;
}

/**
 * Greyscale ramp index for `|=a` … `|=z`.
 *
 * `a` is black (16) and `z` is white (231); the 24 steps between them are the
 * xterm greyscale block 232-255.
 */
function greyIndex(ch: string): number {
  const i = ch.charCodeAt(0) - 97;
  if (i <= 0) return 16;
  if (i >= 25) return 231;
  return 231 + i;
}

/** Six-level colour cube (`|500`, `|[033`) → xterm index. */
function cubeIndex(triplet: string): number {
  return parseInt(triplet, 6) + 16;
}

function esc(text: string): string {
  // Deliberately no &quot;: the server does not escape quotes in text content.
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escAttr(text: string): string {
  return esc(text).replace(/"/g, "&quot;");
}

/** One token of a pipe-coded string. */
type Token =
  | { kind: "text"; value: string }
  | { kind: "break" }
  | { kind: "reset" }
  | { kind: "underline" }
  | { kind: "fg"; index: number }
  | { kind: "bg"; index: number }
  | { kind: "ignore" };

// Order matters: longer/more specific sequences first.
const TOKEN =
  /\|\||\|\/|\|\[[0-5][0-5][0-5]|\|[0-5][0-5][0-5]|\|\[=[a-z]|\|=[a-z]|\|\[[a-zA-Z]|\|[a-zA-Z]/;

function tokenize(text: string): Token[] {
  const out: Token[] = [];
  let rest = text;
  let literal = "";

  const flush = () => {
    if (literal) {
      out.push({ kind: "text", value: literal });
      literal = "";
    }
  };

  while (rest) {
    const m = TOKEN.exec(rest);
    if (!m || m.index === undefined) {
      literal += rest;
      break;
    }
    literal += rest.slice(0, m.index);
    const code = m[0];
    rest = rest.slice(m.index + code.length);

    if (code === "||") {
      literal += "|";
      continue;
    }
    flush();

    if (code === "|/") {
      out.push({ kind: "break" });
    } else if (code === "|n") {
      out.push({ kind: "reset" });
    } else if (code === "|u") {
      out.push({ kind: "underline" });
    } else if (code.startsWith("|[=")) {
      out.push({ kind: "bg", index: greyIndex(code[3]) });
    } else if (code.startsWith("|=")) {
      out.push({ kind: "fg", index: greyIndex(code[2]) });
    } else if (/^\|\[[0-5]{3}$/.test(code)) {
      out.push({ kind: "bg", index: cubeIndex(code.slice(2)) });
    } else if (/^\|[0-5]{3}$/.test(code)) {
      out.push({ kind: "fg", index: cubeIndex(code.slice(1)) });
    } else if (code.startsWith("|[")) {
      const index = BG_INDEX[code[2]];
      out.push(index === undefined ? { kind: "ignore" } : { kind: "bg", index });
    } else {
      const index = FG_INDEX[code[1]];
      out.push(index === undefined ? { kind: "ignore" } : { kind: "fg", index });
    }
  }
  flush();
  return out;
}

/** Convert an Evennia pipe-coded string to HTML, matching the server parser. */
export function pipeToHtml(text: string): string {
  const tokens = tokenize(text ?? "");
  let html = "";
  let fg: number | null = null;
  let bg: number | null = null;
  let underline = false;
  let open = false; // a <span> is currently emitted
  let pending = false; // a style run is armed but has no content yet

  const classList = (): string => {
    const classes: string[] = [];
    if (underline) classes.push("underline");
    if (bg !== null && bg !== DEFAULT_BG) classes.push("bgcolor-" + zfill(String(bg), 3));
    if (fg !== null && fg !== DEFAULT_FG) classes.push("color-" + zfill(String(fg), 3));
    return classes.join(" ");
  };

  const close = () => {
    if (open) {
      html += "</span>";
      open = false;
    }
  };

  for (const token of tokens) {
    switch (token.kind) {
      case "text":
        if (pending) {
          close();
          html += `<span class="${escAttr(classList())}">`;
          open = true;
          pending = false;
        }
        html += esc(token.value);
        break;
      case "break":
        // A newline is emitted inside the current run, as the server does.
        if (pending) {
          close();
          html += `<span class="${escAttr(classList())}">`;
          open = true;
          pending = false;
        }
        html += "<br>";
        break;
      case "reset": {
        // Closing an open run leaves following text bare; a reset with nothing
        // open instead arms an empty run, which is why `|ntext|n` wraps.
        const wasOpen = open;
        close();
        fg = null;
        bg = null;
        underline = false;
        pending = !wasOpen;
        break;
      }
      case "underline":
        underline = true;
        pending = true;
        break;
      case "fg":
        fg = token.index;
        pending = true;
        break;
      case "bg":
        bg = token.index;
        pending = true;
        break;
      case "ignore":
        break;
    }
  }
  close();
  return html;
}

/** True when the string already looks like server-parsed HTML. */
export function looksLikeHtml(text: string): boolean {
  return /<\/?(?:span|div|p|br)\b/i.test(text);
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
