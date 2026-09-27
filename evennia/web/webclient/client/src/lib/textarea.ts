// DOM helpers for the command line's textarea: grow it to its text, and tell
// whether the caret sits on its first or last visual line, which is where Up
// and Down stop moving the caret and start walking the history (history.ts).

/** Styles that decide where a textarea's text wraps. The mirror copies them. */
const WRAP_STYLES = [
  "fontFamily",
  "fontSize",
  "fontWeight",
  "fontStyle",
  "fontVariant",
  "fontStretch",
  "fontKerning",
  "fontFeatureSettings",
  "letterSpacing",
  "wordSpacing",
  "lineHeight",
  "textTransform",
  "textIndent",
  "tabSize",
  "whiteSpace",
  "wordBreak",
  "overflowWrap",
] as const;

/**
 * Whether the caret is on the textarea's first or last visual line.
 *
 * A soft-wrapped line has no newline to find, so the text is laid out again
 * in a hidden copy with the same width and type, with empty markers at the
 * start, at the caret and at the end. Markers on the same line share a top.
 * An empty inline element is no break opportunity, so a word split by the
 * caret still wraps as one word.
 *
 * @param el The textarea.
 * @param edge "first" asks about Up (from the selection's start), "last" about
 *   Down (from its end).
 */
export function caretOnEdge(el: HTMLTextAreaElement, edge: "first" | "last"): boolean {
  const text = el.value;
  const pos = edge === "first" ? el.selectionStart : el.selectionEnd;
  // A hard line break between the caret and that edge settles it.
  if (edge === "first" ? text.lastIndexOf("\n", pos - 1) >= 0 : text.indexOf("\n", pos) >= 0) {
    return false;
  }
  if (pos === (edge === "first" ? 0 : text.length)) return true;

  const cs = getComputedStyle(el);
  const mirror = document.createElement("div");
  const s = mirror.style;
  for (const prop of WRAP_STYLES) s[prop] = cs[prop];
  // clientWidth leaves out a scrollbar, which narrows the field's text too.
  const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  s.boxSizing = "content-box";
  s.width = `${Math.max(0, el.clientWidth - padX)}px`;
  s.padding = "0";
  s.border = "0";
  s.position = "absolute";
  s.top = "0";
  s.left = "-9999px";
  s.visibility = "hidden";
  s.overflow = "hidden";
  const mark = () => mirror.appendChild(document.createElement("span"));
  const start = mark();
  mirror.appendChild(document.createTextNode(text.slice(0, pos)));
  const caret = mark();
  mirror.appendChild(document.createTextNode(text.slice(pos)));
  const end = mark();
  document.body.appendChild(mirror);
  const onEdge = caret.offsetTop === (edge === "first" ? start : end).offsetTop;
  mirror.remove();
  return onEdge;
}

/**
 * Fit a textarea's height to its text. A CSS max-height caps it; past that it
 * scrolls.
 */
export function fitHeight(el: HTMLTextAreaElement): void {
  // Measure without a scrollbar: one showing at the collapsed height narrows
  // the text, wraps it more, and leaves the field a line too tall.
  const overflow = el.style.overflowY;
  el.style.overflowY = "hidden";
  el.style.height = "auto";
  // scrollHeight leaves out borders, which a border-box height includes.
  el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
  el.style.overflowY = overflow;
}
