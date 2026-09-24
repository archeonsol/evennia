// `use:focusOnMount` — focus an element when it enters the DOM.
//
// The HTML `autofocus` attribute only applies while the document is still
// loading. Every input in this shell appears *after* that: the command line
// mounts when the boot sequence finishes, and the search/filter inputs mount
// when their `{#if}` flips. By then the document already has a focused element,
// so the browser ignores `autofocus` and logs
// "Autofocus processing was blocked because a document already has a focused
// element" — the visible symptom being that you have to click before typing.

interface FocusOptions {
  /** Skip focusing (e.g. a panel that should not steal focus when restored). */
  enabled?: boolean;
  /** Select existing content as well, for edit-in-place fields. */
  select?: boolean;
}

export function focusOnMount(node: HTMLElement, options: FocusOptions = {}) {
  if (options.enabled === false) return;
  // Defer a frame: dockview and Svelte both still move nodes around during the
  // mount tick, and focusing an element that is about to be re-parented drops it.
  requestAnimationFrame(() => {
    node.focus({ preventScroll: true });
    if (options.select && node instanceof HTMLInputElement) node.select();
  });
}

/** Whether the event target is a field that owns its own keystrokes. */
export function isTypingTarget(target: unknown): boolean {
  if (!target || typeof target !== "object") return false;
  const el = target as { tagName?: unknown; isContentEditable?: unknown };
  const tag = typeof el.tagName === "string" ? el.tagName.toUpperCase() : "";
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable === true;
}

/** The command line, wherever it is mounted. */
export function commandInput(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-focus-region="input"]');
}

/**
 * Whether a keydown should be handed to the command line from wherever it
 * landed. Clicking the log or a filter chip must not cost a second click before
 * the player can type again, so a plain character typed anywhere that is not a
 * field is redirected. The character still lands in the input: focus moves
 * during keydown, before the browser inserts it.
 *
 * Left alone: modified keys (shortcuts), Space (activates a focused control),
 * Enter and the rest of the named keys, fields, IME composition, and anything
 * while a dialog is open.
 */
export function shouldTypeCommand(
  e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "isComposing">,
  target: unknown,
  modalOpen: boolean,
): boolean {
  if (modalOpen || e.isComposing) return false;
  if (e.ctrlKey || e.metaKey || e.altKey) return false;
  if (e.key.length !== 1 || e.key === " ") return false;
  return !isTypingTarget(target);
}
