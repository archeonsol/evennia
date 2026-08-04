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
// It also mirrors the server's *phase order*, which is load-bearing rather than
// incidental. `parse_html` escapes the text, then substitutes MXP links, and
// only then wraps ANSI runs in spans — so a span that opens inside a link can
// close outside it. `|lc@xp attrs|lt|w[Attributes]|n|n|le` really does yield
// `…<span class="color-015">[Attributes]</span><span class=""></a></span>`,
// with `</a>` inside a span that opened after it. Emitting anchors inline while
// walking tokens would produce well-formed nesting and therefore *not* match, so
// links are substituted against an intermediate string exactly as upstream does.
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

/**
 * Codes the server converts to a literal ANSI escape rather than a span, so the
 * escape lands in the HTML verbatim. Reproduced rather than corrected: parity
 * with `parse_html` is the contract, and "improving" on it here would mean the
 * shell renders something the server never sends.
 */
const RAW_ANSI: Record<string, string> = {
  "|i": "\u001b[3m", // italic on
  "|I": "\u001b[23m", // italic off
  "|s": "\u001b[9m", // strikethrough on
  "|S": "\u001b[29m", // strikethrough off
  "|U": "\u001b[24m", // underline off
};

/** Codes that expand to plain whitespace before any styling is applied. */
const WHITESPACE: Record<string, string> = {
  "|-": "    ", // tab
  "|>": "    ", // tab
  "|_": " ", // hard space
};

/** One token of a pipe-coded string. */
type Token =
  | { kind: "text"; value: string }
  /** Emitted verbatim, bypassing HTML escaping (see `RAW_ANSI`). */
  | { kind: "raw"; value: string }
  /** An MXP marker (`|lc`/`|lu`/`|lt`/`|le`), carried through unescaped so the
   *  link pass can find it. Unmatched markers survive as literal text, which is
   *  what the server's non-greedy triple regex does with them. */
  | { kind: "marker"; value: string }
  | { kind: "break" }
  | { kind: "reset" }
  | { kind: "underline" }
  /** `|h` — brightens the *current* foreground (`|R|h` is bright red). */
  | { kind: "hilite" }
  /** `|H` — the inverse of `|h`, dimming a bright foreground back down. */
  | { kind: "unhilite" }
  /** `|*` — a flag that swaps foreground and background at render time. */
  | { kind: "inverse" }
  | { kind: "blink" }
  | { kind: "fg"; index: number }
  | { kind: "bg"; index: number };

// Order matters: longer/more specific sequences first. `\|l[cute]` must precede
// the generic `\|[a-zA-Z]`, or `|lc` is read as the unknown colour code `|l` and
// the orphaned `c` falls through as literal text — which is exactly how every
// clickable link in the game came to render as "c@xp attrst[Attributes]e".
const TOKEN =
  /\|\||\|\/|\|l[cute]|\|\[[0-5][0-5][0-5]|\|[0-5][0-5][0-5]|\|\[=[a-z]|\|=[a-z]|\|\[[a-zA-Z]|\|[a-zA-Z]|\|[-*^_>]/;

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
    } else if (code.length === 3 && code[1] === "l") {
      out.push({ kind: "marker", value: code });
    } else if (code === "|n") {
      out.push({ kind: "reset" });
    } else if (code === "|u") {
      out.push({ kind: "underline" });
    } else if (code === "|h") {
      out.push({ kind: "hilite" });
    } else if (code === "|H") {
      out.push({ kind: "unhilite" });
    } else if (code === "|*") {
      out.push({ kind: "inverse" });
    } else if (code === "|^") {
      out.push({ kind: "blink" });
    } else if (RAW_ANSI[code] !== undefined) {
      out.push({ kind: "raw", value: RAW_ANSI[code] });
    } else if (WHITESPACE[code] !== undefined) {
      out.push({ kind: "text", value: WHITESPACE[code] });
    } else if (code.startsWith("|[=")) {
      out.push({ kind: "bg", index: greyIndex(code[3]) });
    } else if (code.startsWith("|=")) {
      out.push({ kind: "fg", index: greyIndex(code[2]) });
    } else if (/^\|\[[0-5]{3}$/.test(code)) {
      out.push({ kind: "bg", index: cubeIndex(code.slice(2)) });
    } else if (/^\|[0-5]{3}$/.test(code)) {
      out.push({ kind: "fg", index: cubeIndex(code.slice(1)) });
      // An unrecognised code is *text*, not nothing. The server only rewrites
      // codes it knows and leaves the rest in place, so dropping them silently
      // ate a character of real output every time (`|lz` rendered as "z").
    } else if (code.startsWith("|[")) {
      const index = BG_INDEX[code[2]];
      out.push(index === undefined ? { kind: "text", value: code } : { kind: "bg", index });
    } else {
      const index = FG_INDEX[code[1]];
      out.push(index === undefined ? { kind: "text", value: code } : { kind: "fg", index });
    }
  }
  flush();
  return out;
}

// A style change in the intermediate string is a NUL-delimited index into the
// token list. NUL cannot survive as content (it is stripped on the way in), so
// the link regex can run across the intermediate without a delimiter collision.
const NUL = " ";

/** Escape a group captured by the link regex, as the server does before interpolating. */
function escLinkGroup(group: string): string {
  // The server's text pass already escaped `<&>`; `"` is left alone there and
  // becomes a *backslashed* entity only here, which is why a quote inside a link
  // renders differently from a quote in ordinary text.
  return group.replace(/"/g, "\\&quot;");
}

/**
 * Substitute MXP link markers, matching `text2html`'s two passes and their order.
 *
 * Both patterns are non-greedy and dot-all, so an unmatched marker is left as
 * literal text rather than being consumed.
 */
function substituteLinks(intermediate: string): string {
  let out = intermediate.replace(
    /\|lc([\s\S]*?)\|lt([\s\S]*?)\|le/g,
    (_all, cmd: string, body: string) =>
      `<a id="mxplink" href="#" onclick="Evennia.msg(&quot;text&quot;,` +
      `[&quot;${escLinkGroup(cmd)}&quot;],{});return false;">${escLinkGroup(body)}</a>`,
  );
  out = out.replace(
    /\|lu([\s\S]*?)\|lt([\s\S]*?)\|le/g,
    (_all, url: string, body: string) =>
      `<a id="mxplink" href="${escLinkGroup(url)}" target="_blank">${escLinkGroup(body)}</a>`,
  );
  return out;
}

/** Convert an Evennia pipe-coded string to HTML, matching the server parser. */
export function pipeToHtml(text: string): string {
  // NUL delimits style markers in the intermediate; it has no rendering of its
  // own, so dropping it costs nothing and keeps the markers unambiguous.
  const tokens = tokenize((text ?? "").replace(/ /g, ""));

  // Phase 1: escaped text, literal MXP markers, and style changes as markers.
  let intermediate = "";
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token.kind === "text") intermediate += esc(token.value);
    else if (token.kind === "raw") intermediate += token.value;
    else if (token.kind === "marker") intermediate += token.value;
    else intermediate += `${NUL}${i}${NUL}`;
  }

  // Phase 2: links, before any span is emitted — see the note at the top.
  intermediate = substituteLinks(intermediate);

  // Phase 3: style markers become spans, with the anchor markup now ordinary
  // content that runs are free to open and close across.
  let html = "";
  let fg: number | null = null;
  let bg: number | null = null;
  let underline = false;
  let blink = false;
  let inverse = false;
  let open = false; // a <span> is currently emitted
  let pending = false; // a style run is armed but has no content yet

  const classList = (): string => {
    const classes: string[] = [];
    if (underline) classes.push("underline");
    if (blink) classes.push("blink");
    // `|*` swaps the two channels at render time rather than when it is seen,
    // so a colour set on either side of it lands on the opposite channel.
    const outFg = inverse ? (bg ?? DEFAULT_BG) : fg;
    const outBg = inverse ? (fg ?? DEFAULT_FG) : bg;
    if (outBg !== null && outBg !== DEFAULT_BG) classes.push("bgcolor-" + zfill(String(outBg), 3));
    if (outFg !== null && outFg !== DEFAULT_FG) classes.push("color-" + zfill(String(outFg), 3));
    return classes.join(" ");
  };

  const close = () => {
    if (open) {
      html += "</span>";
      open = false;
    }
  };

  // Odd segments are style-marker indices, even segments are literal output
  // (escaped text plus any anchors the link pass produced).
  const segments = intermediate.split(NUL);
  for (let s = 0; s < segments.length; s++) {
    const segment = segments[s];
    if (s % 2 === 0) {
      if (!segment) continue;
      if (pending) {
        close();
        html += `<span class="${escAttr(classList())}">`;
        open = true;
        pending = false;
      }
      html += segment;
      continue;
    }
    const token = tokens[Number(segment)];
    switch (token.kind) {
      case "text":
      case "marker":
        // Never emitted as markers; handled in phase 1.
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
        blink = false;
        inverse = false;
        pending = !wasOpen;
        break;
      }
      case "underline":
        underline = true;
        pending = true;
        break;
      case "blink":
        blink = true;
        pending = true;
        break;
      case "inverse":
        // Idempotent, not a toggle: `|*|*` renders the same as `|*`.
        inverse = true;
        pending = true;
        break;
      case "hilite":
        // Brightens the base 8 colours only; an xterm-256 index is untouched.
        fg = fg === null ? 15 : fg < 8 ? fg + 8 : fg;
        pending = true;
        break;
      case "unhilite":
        fg = fg === null ? DEFAULT_FG : fg >= 8 && fg < 16 ? fg - 8 : fg;
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
