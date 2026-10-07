// Lore tooltips: the words the game log underlines, and the note each one shows.
//
// The game sends the list once per connect (the `lore:lore_tips` request). A word is
// wrapped when its line lands, so the log itself carries the markup and a hover needs
// no per-line work. Rune-free so it can be unit tested.

import { escapeRegExp } from "./pattern";

export interface LoreTip {
  /** Stable name; the underlined word carries it as `data-lore`. */
  id: string;
  title: string;
  blurb: string;
  /** The help query a click on the note opens. */
  help: string;
  /** Every spelling that lights up, as the log prints it. */
  terms: string[];
  /** Light up whatever the capitals. Off, a term lights up only as written. */
  any_case?: boolean;
  /** Skip a bare word that opens a sentence (a word that is also plain English). */
  mid?: boolean;
}

export interface LoreMatcher {
  /** Every term, longest first, found without regard to case; `exact` then checks the case. */
  re: RegExp;
  exact: Map<string, LoreTip>;
  folded: Map<string, LoreTip>;
  byId: Map<string, LoreTip>;
}

const ID = /^[a-z][a-z0-9_]*$/;
/** More than this is not a glossary, it is a payload gone wrong. */
const MAX_TIPS = 500;
const MAX_TEXT = 2000;

const isText = (value: unknown, max = MAX_TEXT): value is string =>
  typeof value === "string" && value.length > 0 && value.length <= max;

/** The tips in a `lore_tips` reply, dropping anything that is not well formed. */
export function parseTips(raw: unknown): LoreTip[] {
  const list = (raw as { tips?: unknown } | null | undefined)?.tips;
  if (!Array.isArray(list)) return [];
  const out: LoreTip[] = [];
  for (const entry of list.slice(0, MAX_TIPS)) {
    const t = entry as Partial<LoreTip> | null;
    if (!t || typeof t !== "object") continue;
    // The id goes straight into markup, so it is held to what the game promises.
    if (typeof t.id !== "string" || !ID.test(t.id)) continue;
    if (!isText(t.title, 200) || !isText(t.blurb) || !isText(t.help, 200)) continue;
    if (!Array.isArray(t.terms)) continue;
    const terms = t.terms.filter((term): term is string => isText(term, 80));
    if (!terms.length) continue;
    out.push({
      id: t.id,
      title: t.title,
      blurb: t.blurb,
      help: t.help,
      terms,
      ...(t.any_case === true ? { any_case: true } : {}),
      ...(t.mid === true ? { mid: true } : {}),
    });
  }
  return out;
}

/** One matcher for every term, or null when there is nothing to find. */
export function compileLore(tips: LoreTip[]): LoreMatcher | null {
  const exact = new Map<string, LoreTip>();
  const folded = new Map<string, LoreTip>();
  const byId = new Map<string, LoreTip>();
  const terms = new Set<string>();
  for (const tip of tips) {
    byId.set(tip.id, tip);
    for (const term of tip.terms) {
      const map = tip.any_case ? folded : exact;
      const key = tip.any_case ? term.toLowerCase() : term;
      if (!map.has(key)) map.set(key, tip);
      terms.add(term);
    }
  }
  if (!terms.size) return null;
  // Longest first, so "Saint Marrow" wins over "Marrow" where both start together.
  const alternatives = [...terms].sort((a, b) => b.length - a.length || a.localeCompare(b));
  // A word, not part of one: no letter or digit on either side. A hyphen and a
  // letter after the term makes a compound ("Ferro-Fuel", a menu pun), not the Saint.
  const source =
    `(?<![\\p{L}\\p{N}_])(?:${alternatives.map(escapeRegExp).join("|")})` + `(?![\\p{L}\\p{N}_])(?!-[\\p{L}\\p{N}])`;
  return { re: new RegExp(source, "giu"), exact, folded, byId };
}

// A tag (quoted attributes may hold a ">") or a comment, or a run of text.
const TOKEN = /(<!--[\s\S]*?-->|<(?:[^>"']|"[^"]*"|'[^']*')*>)|([^<]+)/g;
// A tag that starts a new line of text, so the next word opens a sentence.
const BREAK = /^<\/?(?:br|p|div|li|ul|ol|tr|td|th|h[1-6]|section|pre|blockquote)\b/i;
const RAW = /^<(script|style|textarea)\b/i;
const QUOTE_ENTITY = /&(?:quot|apos|#34|#39|#x22|#x27);/gi;

/** Whether a word after this text opens a sentence. Quotes and brackets do not count. */
function opensSentence(before: string): boolean {
  const trimmed = before.replace(QUOTE_ENTITY, '"').replace(/[\s"'“”‘’(\[]+$/u, "");
  return trimmed === "" || /[.!?…]$/u.test(trimmed);
}

function wrapText(text: string, before: string, m: LoreMatcher): string {
  const re = m.re;
  re.lastIndex = 0;
  let out = "";
  let last = 0;
  let hit: RegExpExecArray | null;
  while ((hit = re.exec(text))) {
    const word = hit[0];
    const tip = m.exact.get(word) ?? m.folded.get(word.toLowerCase());
    // Only a bare word is held back at the start of a sentence: "Saint Marrow" is not
    // ordinary English, wherever it stands.
    const held = tip?.mid && !/\s/.test(word) && opensSentence((before + text.slice(0, hit.index)).slice(-16));
    if (!tip || held) {
      // Not this word, or not here. Look again one letter on: "saint Marrow" is
      // not "Saint Marrow", but the bare name inside it still lights up.
      re.lastIndex = hit.index + 1;
      continue;
    }
    out += `${text.slice(last, hit.index)}<span class="lore" data-lore="${tip.id}">${word}</span>`;
    last = hit.index + word.length;
  }
  return last === 0 ? text : out + text.slice(last);
}

/**
 * Mark every lore word in a line of log HTML. Only text is touched: a word inside a
 * tag or an attribute is left alone, and the plain text of the line does not change.
 */
export function wrapLore(html: string, matcher: LoreMatcher | null): string {
  if (!matcher || !html) return html;
  // Most lines hold no lore word; the cheap whole-line test saves the walk for them.
  matcher.re.lastIndex = 0;
  const maybe = matcher.re.test(html);
  matcher.re.lastIndex = 0;
  if (!maybe) return html;

  let before = "";
  let raw: string | null = null;
  return html.replace(TOKEN, (_all, tag: string | undefined, text: string | undefined) => {
    if (tag !== undefined) {
      if (raw) {
        if (new RegExp(`^</${raw}\\s*>`, "i").test(tag)) raw = null;
      } else {
        const open = RAW.exec(tag);
        if (open) raw = open[1].toLowerCase();
        else if (BREAK.test(tag)) before = "";
      }
      return tag;
    }
    if (raw || text === undefined) return text ?? "";
    const wrapped = wrapText(text, before, matcher);
    before = (before + text).slice(-16);
    return wrapped;
  });
}
