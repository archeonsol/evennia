// `use:modal` - the keyboard contract every dialog in the shell owes: focus
// moves in when it opens, Tab stays inside, Escape closes, and focus goes back
// to whatever had it before. The dialogs had none of it, so a keyboard user
// who opened Settings was still typing into the command line behind it.

interface ModalOptions {
  /** Called on Escape. Omit for a dialog Escape must not dismiss. */
  onclose?: () => void;
  /** Element to focus on open; defaults to the first focusable one. */
  initial?: HTMLElement | null;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
  'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) => !el.hidden && el.offsetParent !== null,
  );
}

export function modal(node: HTMLElement, options: ModalOptions = {}) {
  let opts = options;
  const returnTo = document.activeElement as HTMLElement | null;

  // Deferred, like focusOnMount: the dialog is still being built. A frame,
  // or a beat when frames are paused, whichever comes first.
  let placed = false;
  const place = () => {
    if (placed) return;
    placed = true;
    if (node.contains(document.activeElement)) return;
    (opts.initial ?? focusables(node)[0] ?? node).focus();
  };
  requestAnimationFrame(place);
  setTimeout(place, 50);

  function onKey(e: KeyboardEvent) {
    if (e.defaultPrevented) return;
    if (e.key === "Escape" && opts.onclose) {
      // A control that uses Escape itself (a key-capture field) stops it first.
      e.preventDefault();
      e.stopPropagation();
      opts.onclose();
      return;
    }
    if (e.key !== "Tab") return;
    const list = focusables(node);
    if (!list.length) {
      e.preventDefault();
      return;
    }
    const first = list[0];
    const last = list[list.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !node.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !node.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  }

  node.addEventListener("keydown", onKey);
  return {
    update(next: ModalOptions) {
      opts = next;
    },
    destroy() {
      node.removeEventListener("keydown", onKey);
      // Only hand focus back if it would otherwise be lost to the page body.
      if (returnTo?.isConnected && (!document.activeElement || document.activeElement === document.body || node.contains(document.activeElement))) {
        returnTo.focus();
      }
    },
  };
}
