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
