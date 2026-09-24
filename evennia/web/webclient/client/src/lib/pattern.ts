// One parser for every player-written match pattern (highlights, gags,
// actions, routing). Rune-free so it can be unit tested.
//
// The settings fields say "text or /regex/", and that is now what they do:
//
//   whispers          plain text, matched literally, any case
//   /^\[Nous\]/       a regular expression, any case
//   /Nous/u           flags written out: exactly those flags (so case-sensitive)
//
// Before, the raw field was handed to RegExp: "/foo/" only matched text with
// literal slashes in it, plain text with a "." or "(" in it matched the
// wrong lines, and a broken regex quietly became literal text, so a rule
// could look fine and never fire with nothing saying why.

export interface ParsedPattern {
  /** Compiled with the g flag (highlighting replaces every match); null when unusable. */
  re: RegExp | null;
  /** Why the pattern cannot be used, for the settings field; "" when fine. */
  error: string;
  /** True for a /regex/ pattern, false for plain text. */
  regex: boolean;
}

const SLASHED = /^\/(.+)\/([a-z]*)$/s;
const ALLOWED_FLAGS = /^[imsu]*$/;

export function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function parsePattern(pattern: string): ParsedPattern {
  const p = (pattern || "").trim();
  if (!p) return { re: null, error: "", regex: false };
  const m = SLASHED.exec(p);
  if (!m) return { re: new RegExp(escapeRegExp(p), "gi"), error: "", regex: false };

  const [, body, written] = m;
  if (!ALLOWED_FLAGS.test(written)) {
    return { re: null, error: "Unknown flag. Use i, m, s or u.", regex: true };
  }
  // No flags written: any case, as plain text is. Flags written: exactly those.
  const flags = "g" + (written ? [...new Set(written)].join("") : "i");
  let re: RegExp;
  try {
    re = new RegExp(body, flags);
  } catch {
    return { re: null, error: "Not a valid regex.", regex: true };
  }
  // A pattern that matches nothing at all ("", "a*", "x|") matches every line:
  // as a route it would file the whole game into one feed.
  re.lastIndex = 0;
  if (re.test("")) {
    return { re: null, error: "Matches every line.", regex: true };
  }
  re.lastIndex = 0;
  return { re, error: "", regex: true };
}

/** Test one line against a compiled pattern, safe for the stateful g flag. */
export function matches(re: RegExp, text: string): boolean {
  re.lastIndex = 0;
  const hit = re.test(text);
  re.lastIndex = 0;
  return hit;
}

// Characters that only mean something in a regex. A saved pattern carrying
// one of these, with no slashes, was written as a regex under the old rules.
const REGEX_ONLY = /\\|\^|\$|\||\[|\(|\*|\+|\{/;

/**
 * Upgrade a pattern saved under the old rules, where the raw field was the
 * regex. One that plainly is a regex (and compiles) is wrapped in slashes so
 * it keeps matching what it matched; everything else is left as text.
 */
export function migrateLegacyPattern(pattern: string): string {
  const p = (pattern || "").trim();
  if (!p || SLASHED.test(p) || !REGEX_ONLY.test(p)) return pattern;
  try {
    new RegExp(p, "gi");
  } catch {
    return pattern; // it was already being treated as literal text
  }
  return `/${p}/i`;
}
