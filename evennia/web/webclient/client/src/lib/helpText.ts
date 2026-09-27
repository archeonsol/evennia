// Help text for the help panel: authored pipe markup to HTML, with every
// `|whelp <topic>|n` cross-reference turned into an in-panel link.
//
// A help link must not send a command: that would print into the terminal and
// go through the parser. So the reference is parked as a sentinel before the
// markup renderer runs (which escapes text and would mangle an anchor written
// in), then swapped for an anchor carrying the topic in `data-help`. The panel
// catches clicks on `.help-link` and opens the topic itself.

import { pipeToHtml } from "./markup";

const HELP_LINK = /\|whelp ([^|<>\[\]]+?)\|n/g;
const PARKED = /\u0002([^\u0003]*)\u0003/g;

function escAttr(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const SYNTAX_LINE = /^\|w.*\|n$/;

/**
 * Join hard-wrapped prose so it reflows to the panel's width.
 *
 * Help is written for an 80-column terminal, so a paragraph arrives as several
 * lines. In a narrow panel those breaks leave a ragged edge. A paragraph made
 * only of prose is joined into one line; a paragraph holding a syntax line or
 * an indented line (example output, a list) keeps its lines as written.
 */
export function reflow(text: string): string {
  return String(text)
    .split(/\n[ \t]*\n/)
    .map((block) => {
      const lines = block.split("\n");
      const keep = lines.some((line) => SYNTAX_LINE.test(line.trim()) || /^\s/.test(line));
      return keep ? block : lines.map((line) => line.trim()).join(" ");
    })
    .join("\n\n");
}

/** Pipe-coded help text as HTML, with help references as panel links. */
export function helpTextToHtml(text: string | undefined | null): string {
  if (!text) return "";
  const parked = reflow(String(text)).replace(HELP_LINK, (_m, target: string) => `\u0002${target.trim()}\u0003`);
  return pipeToHtml(parked).replace(PARKED, (_m, target: string) => {
    // The markup renderer escaped the target; undo that for the attribute.
    const raw = target.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
    return `<a class="help-link" href="#" data-help="${escAttr(raw)}">help ${target}</a>`;
  });
}

/** Colour codes and link markup removed: what a reader sees, as plain text. */
export function helpPlain(text: string | undefined | null): string {
  if (!text) return "";
  return String(text)
    .replace(/\|\|/g, "\u0000")
    .replace(/\|l[cu].*?\|lt|\|le/g, "")
    .replace(/\|(?:\[?#[0-9a-fA-F]{3,6}|\[?[0-5]{3}|\[?=[a-z]|\[?[a-zA-Z*^/_\-])/g, "")
    .replace(/\u0000/g, "|");
}

/** An id-safe anchor for a section title. */
export function sectionAnchor(title: string): string {
  return "help-sec-" + String(title).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
