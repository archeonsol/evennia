// Client-side Evennia markup → HTML.
//
// The server normally ships pre-parsed `html` alongside each node body, but it
// will omit it for a client declaring `caps.rendersMarkup` — and several panels
// already fall back here whenever `html` is absent. Either way the output must
// match `evennia.utils.text2html.parse_html` exactly, so this mirrors that
// parser's rules rather than approximating them:
//
// Pipe codes are not the only input. `parse_html` runs `parse_ansi` first, so
// raw ANSI escapes already in the body are styled exactly like the codes that
// produce them — and plenty of body text arrives that way, because anything
// built through `ANSIString` (EvTable, EvForm, EvMenu) has already had its pipe
// codes resolved to escapes by the time `str()` is called on it. Those escapes
// are tokenized here alongside the pipe codes; an escape the server does not
// recognise stays literal text, as it does there.
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

/** Width `|-`, `|>` and a literal tab all expand to, matching `tabstop`. */
const TABSTOP = 4;

/**
 * The server's text pass: escape HTML metacharacters, expand tabs, and turn
 * newlines into `<br>` — all before links or spans are considered.
 *
 * The newline rule is not cosmetic. A body carrying real newlines rendered as
 * one run-on line in the shell, because `\n` collapses to whitespace in HTML,
 * and it also changed what the URL pass saw: `https://x.com.\n` links the
 * trailing dot once the newline is a tag, and does not while it is still a
 * space.
 */
function subText(text: string): string {
  return text.replace(/[<&>]|\t+|\r\n|\r|\n/g, (match) => {
    if (match === "<") return "&lt;";
    if (match === "&") return "&amp;";
    if (match === ">") return "&gt;";
    if (match[0] === "\t") return " ".repeat(TABSTOP).repeat(match.length);
    return "<br>";
  });
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
  /** A recognised code that changes no state. It still arms a new run: the
   *  server erases the escape from the output, and an erased escape is what
   *  tells it the next text belongs to a fresh span. */
  | { kind: "restyle" }
  | { kind: "underline" }
  /** `|h` — turns the hilite flag on, brightening the current base colour. */
  | { kind: "hilite" }
  /** `|H` — the inverse of `|h`, dimming a bright foreground back down. */
  | { kind: "unhilite" }
  /** `|*` — a flag that swaps foreground and background at render time. */
  | { kind: "inverse" }
  | { kind: "blink" }
  /**
   * A foreground colour. `base` is 0-7 for one of the eight ANSI colours, which
   * the hilite flag brightens to 8-15, or 16-255 for an xterm index, which
   * hilite never touches.
   *
   * The split matters: the server keeps `fg` and `hilight` as separate state and
   * combines them only when it emits a span, so `\x1b[1m\x1b[37m` — the header
   * cell every EvTable emits — is bright white, not the default grey a single
   * collapsed index would give.
   */
  | { kind: "fg"; base: number }
  | { kind: "bg"; index: number }
  /** A truecolor channel, as the `#rrggbb` the server's inline style would use. */
  | { kind: "tcfg"; hex: string }
  | { kind: "tcbg"; hex: string };

/**
 * Non-colour ANSI escapes the server's `re_style` knows, and the state changes
 * each makes. The combined forms are single codes on the wire — `\x1b[1;5;7m`
 * is one alternative in that pattern, not three separate escapes.
 */
const ANSI_STYLE: Record<string, Token[]> = {
  "\u001b[0m": [{ kind: "reset" }],
  "\u001b[4m": [{ kind: "underline" }],
  "\u001b[1m": [{ kind: "hilite" }],
  "\u001b[22m": [{ kind: "unhilite" }],
  "\u001b[5m": [{ kind: "blink" }],
  "\u001b[7m": [{ kind: "inverse" }],
  "\u001b[1;7m": [{ kind: "hilite" }, { kind: "inverse" }],
  // Not a typo, and not symmetric with the others. format_styles tests
  // membership against three hand-written tuples, and the combined codes are
  // not all in the tuples their name implies: BLINK_HILITE is missing from
  // the hilite tuple, and INV_BLINK from both the inverse and the blink one.
  // So `\x1b[1;5m` blinks without brightening and `\x1b[7;5m` does neither --
  // it only counts as a style change. Reproduced, not corrected.
  "\u001b[1;5m": [{ kind: "blink" }],
  "\u001b[7;5m": [{ kind: "restyle" }],
  "\u001b[1;5;7m": [{ kind: "hilite" }, { kind: "blink" }, { kind: "inverse" }],
};

const RE_ANSI_NAMED = /^\u001b\[([34])([0-7])m$/;
const RE_ANSI_XTERM = /^\u001b\[([34])8;5;([0-9]{1,3})m$/;
const RE_ANSI_TRUECOLOR = /^\u001b\[([34])8;2;([0-9]{1,3});([0-9]{1,3});([0-9]{1,3})m$/;
/** The server's truecolor byte pattern: up to three digits, first of them 0-2. */
const RE_ANSI_BYTE = /^[0-2]?[0-9]?[0-9]$/;

function hexByte(value: string): string {
  return Number(value).toString(16).padStart(2, "0");
}

/**
 * Tokens for one raw ANSI escape, or `null` when the server would not recognise
 * it. `null` is not "ignore": an unrecognised escape is ordinary text to
 * `format_styles`, so it survives into the output and counts as content.
 */
function ansiTokens(code: string): Token[] | null {
  const style = ANSI_STYLE[code];
  if (style) return style;

  const named = RE_ANSI_NAMED.exec(code);
  if (named) {
    const index = Number(named[2]);
    return named[1] === "3" ? [{ kind: "fg", base: index }] : [{ kind: "bg", index }];
  }

  const xterm = RE_ANSI_XTERM.exec(code);
  if (xterm) {
    // The server's tables hold 16-255 only, so `\x1b[38;5;7m` is not a code it
    // knows and stays literal even though it is valid SGR.
    const index = Number(xterm[2]);
    if (index < 16 || index > 255) return null;
    return xterm[1] === "3" ? [{ kind: "fg", base: index }] : [{ kind: "bg", index }];
  }

  const truecolor = RE_ANSI_TRUECOLOR.exec(code);
  if (truecolor) {
    const [, channel, r, g, b] = truecolor;
    if (!RE_ANSI_BYTE.test(r) || !RE_ANSI_BYTE.test(g) || !RE_ANSI_BYTE.test(b)) return null;
    const hex = `#${hexByte(r)}${hexByte(g)}${hexByte(b)}`;
    return [channel === "3" ? { kind: "tcfg", hex } : { kind: "tcbg", hex }];
  }

  return null;
}

// Order matters: longer/more specific sequences first. `\|l[cute]` must precede
// the generic `\|[a-zA-Z]`, or `|lc` is read as the unknown colour code `|l` and
// the orphaned `c` falls through as literal text — which is exactly how every
// clickable link in the game came to render as "c@xp attrst[Attributes]e".
//
// The escape alternatives lead, and are deliberately looser than the server's
// tables (any 1-3 digit parameter); `ansiTokens` does the exact range check and
// hands back a literal for anything out of range. Matching loosely and rejecting
// late is the same output as never matching, and keeps the ranges in one place.
const TOKEN =
  /\u001b\[(?:1;5;7|1;5|1;7|7;5|22|0|1|4|5|7)m|\u001b\[[34]8;5;[0-9]{1,3}m|\u001b\[[34]8;2;[0-9]{1,3};[0-9]{1,3};[0-9]{1,3}m|\u001b\[[34][0-7]m|\|\||\|\/|\|l[cute]|\|\[[0-5][0-5][0-5]|\|[0-5][0-5][0-5]|\|\[=[a-z]|\|=[a-z]|\|\[[a-zA-Z]|\|[a-zA-Z]|\|[-*^_>]/;

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

    if (code.charCodeAt(0) === 27) {
      const ansi = ansiTokens(code);
      if (ansi) out.push(...ansi);
      else out.push({ kind: "text", value: code });
      continue;
    }

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
      out.push({ kind: "fg", base: greyIndex(code[2]) });
    } else if (/^\|\[[0-5]{3}$/.test(code)) {
      out.push({ kind: "bg", index: cubeIndex(code.slice(2)) });
    } else if (/^\|[0-5]{3}$/.test(code)) {
      out.push({ kind: "fg", base: cubeIndex(code.slice(1)) });
      // An unrecognised code is *text*, not nothing. The server only rewrites
      // codes it knows and leaves the rest in place, so dropping them silently
      // ate a character of real output every time (`|lz` rendered as "z").
    } else if (code.startsWith("|[")) {
      const index = BG_INDEX[code[2]];
      out.push(index === undefined ? { kind: "text", value: code } : { kind: "bg", index });
    } else {
      const index = FG_INDEX[code[1]];
      if (index === undefined) {
        out.push({ kind: "text", value: code });
      } else {
        // A named code carries both channels — `|r` is HILITE+RED on the wire
        // and `|R` is UNHILITE+RED — so emit the two state changes the server
        // sees rather than one collapsed index. Folding them together loses the
        // hilite flag, and a later `|h` or bare colour code then reads wrong.
        out.push({ kind: index >= 8 ? "hilite" : "unhilite" });
        out.push({ kind: "fg", base: index & 7 });
      }
    }
  }
  flush();
  return out;
}

// A style change in the intermediate string is a NUL-delimited index into the
// token list. NUL cannot survive as content (it is stripped on the way in), so
// the link regex can run across the intermediate without a delimiter collision.
const NUL = "\u0000";

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

// Bare-URL auto-linking, upstream's final pass. Deliberately faithful to two
// quirks: it uses `search`, so only the *first* URL in a string is linked and
// any others stay plain text; and a bare host that fails validation makes it
// bail on the whole string rather than just that match.
//
// The upstream pattern opens with a `(?<!=")` lookbehind to avoid re-linking an
// href it already emitted. That is checked by hand here instead — lookbehind is
// a syntax error in older Safari, and an unsupported regex literal would fail to
// parse and take the whole bundle down rather than degrade.
const RE_URL =
  /\b(?:ftp|www|https?)\W+(?:(?!\.(?:\s|$)|&\w+;)[^"',;$*^\\(){}<>[\]\s])+(\.(?:\s|$)|&\w+;|)/g;
const RE_PROTOCOL = /^(?:ftp|https?):\/\//;
const RE_VALID_NO_PROTOCOL =
  /^(?:www|ftp)\.[-a-zA-Z0-9@:%._+~#=]{2,256}\.[a-z]{2,6}\b[-a-zA-Z0-9@:%_+.~#?&//=]*/;

function convertUrls(text: string): string {
  RE_URL.lastIndex = 0;
  for (let m = RE_URL.exec(text); m; m = RE_URL.exec(text)) {
    // Stand-in for the lookbehind: skip a match that is already an href value.
    if (text.slice(m.index - 2, m.index) === '="') continue;
    const rest = m[1] ?? "";
    // Group 1 is the trailing punctuation, which sits outside the anchor.
    const label = m[0].slice(0, m[0].length - rest.length);
    let href = label;
    if (!RE_PROTOCOL.test(href)) {
      if (!RE_VALID_NO_PROTOCOL.test(href)) return text;
      href = "http://" + href;
    }
    return (
      text.slice(0, m.index) +
      `<a href="${href}" target="_blank">${label}</a>${rest}` +
      text.slice(m.index + m[0].length)
    );
  }
  return text;
}

/**
 * Upstream `remove_backspaces`, run over the finished HTML exactly as it is
 * there: a backspace eats the character before it, and `ESC[K` is dropped.
 *
 * The loop is not a global replace. Upstream substitutes one match at a time
 * and starts over, so `ab\b\b` collapses all the way instead of leaving the
 * first backspace stranded. `.` there excludes a newline, hence the class.
 */
function removeBackspaces(text: string): string {
  const pattern = /[^\n]\u0008|\u001b\[K/;
  let out = text;
  for (let m = pattern.exec(out); m; m = pattern.exec(out)) {
    out = out.slice(0, m.index) + out.slice(m.index + m[0].length);
  }
  return out;
}

/**
 * Convert an Evennia-marked-up string to HTML, matching the server parser.
 *
 * Accepts pipe codes, raw ANSI escapes, or both in the same string — the server
 * resolves the former to the latter before it styles anything.
 */
export function pipeToHtml(text: string): string {
  // NUL delimits style markers in the intermediate; it has no rendering of its
  // own, so dropping it costs nothing and keeps the markers unambiguous.
  // BEL goes with it: `remove_bells` drops it before styling, so it never
  // counts as the content that opens a span.
  const tokens = tokenize((text ?? "").replace(/[\u0000\u0007]/g, ""));

  // Phase 1: escaped text, literal MXP markers, and style changes as markers.
  let intermediate = "";
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token.kind === "text") intermediate += subText(token.value);
    else if (token.kind === "raw") intermediate += token.value;
    else if (token.kind === "marker") intermediate += token.value;
    else intermediate += `${NUL}${i}${NUL}`;
  }

  // Phase 2: links, before any span is emitted — see the note at the top.
  intermediate = substituteLinks(intermediate);

  // Phase 3: style markers become spans, with the anchor markup now ordinary
  // content that runs are free to open and close across.
  let html = "";
  let fgBase: number | null = null;
  let bg: number | null = null;
  let hilite = false;
  let underline = false;
  let blink = false;
  let inverse = false;
  let tcFg: string | null = null;
  let tcBg: string | null = null;
  let open = false; // a <span> is currently emitted
  let pending = false; // a style run is armed but has no content yet

  /** The current foreground as one index, hilite folded in at emit time. */
  const fgIndex = (): number => {
    // Absent a colour the server's default is unhilited white, so `|h` on its
    // own is bright white rather than nothing.
    const base = fgBase ?? DEFAULT_FG;
    // Only the eight ANSI colours brighten; an xterm index is already absolute.
    return base < 8 && hilite ? base + 8 : base;
  };

  const openTag = (): string => {
    const classes: string[] = [];
    if (underline) classes.push("underline");
    if (blink) classes.push("blink");
    // `|*` swaps the two channels at render time rather than when it is seen,
    // so a colour set on either side of it lands on the opposite channel.
    const outFg = inverse ? (bg ?? DEFAULT_BG) : fgIndex();
    const outBg = inverse ? fgIndex() : bg;
    if (outBg !== null && outBg !== DEFAULT_BG) classes.push("bgcolor-" + zfill(String(outBg), 3));
    if (outFg !== DEFAULT_FG) classes.push("color-" + zfill(String(outFg), 3));
    const cls = escAttr(classes.join(" "));

    // Truecolor rides as an inline style *alongside* the classes, which the
    // server also keeps emitting — `blink` and `underline` have no truecolor
    // equivalent, and it computes the colour classes before it knows whether a
    // style is needed.
    //
    // Divergence, deliberate: the server's inverse+truecolor branches leave
    // `bg_class`/`color_class` unassigned and reuse whatever the previous span
    // left in those locals, and they mutate the truecolor state permanently.
    // That is not a behaviour worth reproducing, so the channels are swapped
    // here and the classes come from the ordinary inverse path.
    const styleFg = inverse ? tcBg : tcFg;
    const styleBg = inverse ? tcFg : tcBg;
    if (styleFg === null && styleBg === null) return `<span class="${cls}">`;
    let style = "";
    if (styleFg !== null) style += `color: ${styleFg};`;
    if (styleBg !== null) style += `background-color: ${styleBg};`;
    return `<span class="${cls}" style="${style}">`;
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
        html += openTag();
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
          html += openTag();
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
        fgBase = null;
        bg = null;
        hilite = false;
        underline = false;
        blink = false;
        inverse = false;
        tcFg = null;
        tcBg = null;
        pending = !wasOpen;
        break;
      }
      case "restyle":
        pending = true;
        break;
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
        // A flag, not a colour: it brightens whichever base colour is current
        // when the span is emitted, including one set after this point.
        hilite = true;
        pending = true;
        break;
      case "unhilite":
        hilite = false;
        pending = true;
        break;
      case "fg":
        fgBase = token.base;
        pending = true;
        break;
      case "bg":
        bg = token.index;
        pending = true;
        break;
      case "tcfg":
        tcFg = token.hex;
        pending = true;
        break;
      case "tcbg":
        tcBg = token.hex;
        pending = true;
        break;
    }
  }
  close();
  // Phase 4: backspaces, then bare-URL auto-linking. Upstream runs both over
  // the finished HTML, spans and anchors included, and in this order.
  return convertUrls(removeBackspaces(html));
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
