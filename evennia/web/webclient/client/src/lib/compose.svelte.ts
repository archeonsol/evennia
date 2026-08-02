// Compose-pad state: the draft, its mode, and the live preview the server
// returns for it. Persisted so a draft survives closing the pad and reloading
// the page - losing a half-written pose to a stray Esc is the thing players
// actually complain about.
//
// The mode vocabulary and command mapping live in the rune-free
// `compose-modes.ts`; this is the reactive container plus the debounce.

import { composeToPreview, type ComposeMode } from "./compose-modes";

const KEY = "underspire.compose.draft.v1";
//: The pad requests a preview this long after the last keystroke. Long enough
//: that ordinary typing does not put a command per character on the wire.
const PREVIEW_DEBOUNCE_MS = 520;

export interface ComposePreview {
  you: string;
  room: string;
  error: string;
}

class Compose {
  open = $state(false);
  mode = $state<ComposeMode>("pose");
  text = $state("");
  preview = $state<ComposePreview>({ you: "", room: "", error: "" });

  private timer: ReturnType<typeof setTimeout> | null = null;
  private send: ((line: string) => void) | null = null;

  /** Restore the persisted draft. The pad itself never auto-reopens. */
  init(): void {
    try {
      const d = JSON.parse(localStorage.getItem(KEY) || "{}");
      this.mode = (d.mode as ComposeMode) || "pose";
      this.text = typeof d.text === "string" ? d.text : "";
    } catch {
      /* first run / unwritable storage */
    }
  }

  /** Wire the preview request to the live connection (done once at startup). */
  setPreviewSender(send: (line: string) => void): void {
    this.send = send;
  }

  /** True when there is a draft worth showing an indicator for. */
  get hasDraft(): boolean {
    return this.text.trim().length > 0;
  }

  setMode(mode: ComposeMode): void {
    this.mode = mode;
    this.persist();
    this.schedulePreview();
  }

  setText(text: string): void {
    this.text = text;
    this.persist();
    this.schedulePreview();
  }

  show(): void {
    this.open = true;
    this.schedulePreview();
  }

  hide(): void {
    this.persist();
    this.cancelPreview();
    this.open = false;
  }

  /** Consume the draft as a command line, clearing it. Returns "" if empty. */
  take(): string {
    const text = this.text.trim();
    if (!text) return "";
    this.text = "";
    this.preview = { you: "", room: "", error: "" };
    this.persist();
    this.cancelPreview();
    this.open = false;
    return text;
  }

  /** Apply a `compose_preview` OOB payload. */
  applyPreview(payload: Partial<ComposePreview> | null | undefined): void {
    const p = payload ?? {};
    this.preview = {
      you: String(p.you ?? ""),
      room: String(p.room ?? ""),
      error: String(p.error ?? ""),
    };
  }

  private persist(): void {
    try {
      // `open` is deliberately not persisted: a refresh should not put the pad
      // back over the game log.
      localStorage.setItem(KEY, JSON.stringify({ mode: this.mode, text: this.text }));
    } catch {
      /* unwritable storage is not worth failing a keystroke over */
    }
  }

  private cancelPreview(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private schedulePreview(): void {
    this.cancelPreview();
    if (!this.open) return;
    const line = composeToPreview(this.mode, this.text);
    if (!line) {
      this.preview = { you: "", room: "", error: "" };
      return;
    }
    this.timer = setTimeout(() => {
      this.timer = null;
      this.send?.(line);
    }, PREVIEW_DEBOUNCE_MS);
  }
}

export const compose = new Compose();
