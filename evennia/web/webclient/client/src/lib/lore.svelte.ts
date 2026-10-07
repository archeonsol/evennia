// The lore store: the words the log underlines, and the note over the one in focus.
//
// The words come from the game once per connect and are marked as each line lands
// (session.append), so the log carries the markup and one listener on the document
// serves every panel that shows a line. A note opens on a hover after a short pause,
// or on a tap, and its "Read more" opens the help page behind it.

import { connection } from "./evennia.svelte";
import { compileLore, parseTips, wrapLore, type LoreMatcher, type LoreTip } from "./lore";
import { settings } from "./settings.svelte";

/** A pointer crossing the log must rest on a word this long before its note opens. */
const SHOW_DELAY_MS = 250;
/** With a note already open, moving to another word switches almost at once. */
const SWITCH_DELAY_MS = 60;
/** The pointer has this long to get from the word onto its note before it closes. */
const HIDE_DELAY_MS = 200;

export interface ActiveTip {
  tip: LoreTip;
  /** The word's box in the window, which the note sits over. */
  rect: { left: number; top: number; right: number; bottom: number };
  /** Opened by a tap: stays until something else is tapped. */
  pinned: boolean;
}

/** The box of a word to sit the note over: the line piece under the pointer, when it wrapped. */
function rectOf(el: Element, x?: number, y?: number): ActiveTip["rect"] {
  const boxes = Array.from(el.getClientRects());
  const under = x === undefined || y === undefined ? undefined : boxes.find((r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom);
  const r = under ?? boxes[0] ?? el.getBoundingClientRect();
  return { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
}

class LoreStore {
  /** The note on show, if any. */
  active = $state<ActiveTip | null>(null);

  private matcher: LoreMatcher | null = null;
  private target: Element | null = null;
  private showTimer: ReturnType<typeof setTimeout> | null = null;
  private hideTimer: ReturnType<typeof setTimeout> | null = null;
  private pointer = "mouse";
  private bound = false;

  /** Whether any word is known. */
  get ready(): boolean {
    return this.matcher !== null;
  }

  private get enabled(): boolean {
    return settings.loreTips && !settings.screenreader;
  }

  /** Take the game's list of words, replacing the one before it. */
  setTips(raw: unknown): void {
    this.matcher = compileLore(parseTips(raw));
    this.hide(true);
  }

  /** Mark the lore words in a line of log HTML. Screen reader mode marks none. */
  wrap(html: string): string {
    return settings.screenreader ? html : wrapLore(html, this.matcher);
  }

  /** Ask the game for its words. A game that has none, or a dropped link, leaves the log plain. */
  async load(): Promise<void> {
    try {
      this.setTips(await connection.request("lore", "lore_tips"));
    } catch {
      /* offline, or a game without lore notes */
    }
  }

  /** Listen on the document for words being hovered or tapped. Once, from main.ts. */
  init(): void {
    if (this.bound || typeof document === "undefined") return;
    this.bound = true;
    document.addEventListener(
      "pointerdown",
      (e) => {
        this.pointer = e.pointerType || "mouse";
        // Anywhere but a word or its note closes the note.
        if (this.active && !(e.target instanceof Element && e.target.closest(".lore, .lore-tip"))) this.hide(true);
      },
      true,
    );
    document.addEventListener("pointerover", (e) => this.onOver(e));
    document.addEventListener("pointerout", (e) => this.onOut(e));
    document.addEventListener("click", (e) => this.onClick(e));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") this.hide(true);
    });
    // A note is placed over a word's box, so anything that moves the word loses it.
    window.addEventListener("scroll", () => this.hide(true), true);
    window.addEventListener("resize", () => this.hide(true));
    window.addEventListener("blur", () => this.hide(true));
    $effect.root(() => {
      $effect(() => {
        if (!this.enabled) this.hide(true);
      });
    });
  }

  private word(target: EventTarget | null): HTMLElement | null {
    return target instanceof Element ? target.closest<HTMLElement>(".lore[data-lore]") : null;
  }

  private onOver(e: PointerEvent): void {
    if (e.pointerType !== "mouse" || !this.enabled) return;
    const el = this.word(e.target);
    if (!el) return;
    if (this.hideTimer) this.hold();
    if (this.active && this.target === el) return;
    this.schedule(el, e.clientX, e.clientY, this.active ? SWITCH_DELAY_MS : SHOW_DELAY_MS);
  }

  private onOut(e: PointerEvent): void {
    if (e.pointerType !== "mouse") return;
    const el = this.word(e.target);
    if (!el) return;
    // Moving between the pieces of one word is not leaving it.
    if (e.relatedTarget instanceof Node && el.contains(e.relatedTarget)) return;
    if (this.showTimer) {
      clearTimeout(this.showTimer);
      this.showTimer = null;
    }
    if (this.active && !this.active.pinned) this.hide();
  }

  /** A tap, or a pen, opens the note and leaves it open: there is no hover to rest on. */
  private onClick(e: MouseEvent): void {
    if (this.pointer === "mouse" || !this.enabled) return;
    const el = this.word(e.target);
    if (!el) return;
    if (this.active?.pinned && this.target === el) this.hide(true);
    else this.show(el, true, e.clientX, e.clientY);
  }

  private schedule(el: HTMLElement, x: number, y: number, delay: number): void {
    if (this.showTimer) clearTimeout(this.showTimer);
    this.showTimer = setTimeout(() => {
      this.showTimer = null;
      this.show(el, false, x, y);
    }, delay);
  }

  private show(el: HTMLElement, pinned: boolean, x?: number, y?: number): void {
    const tip = this.matcher?.byId.get(el.dataset.lore ?? "");
    if (!tip) return;
    this.hide(true);
    this.target = el;
    this.active = { tip, rect: rectOf(el, x, y), pinned };
  }

  /** The pointer is on the note itself: keep it open. */
  hold(): void {
    if (this.hideTimer) {
      clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
  }

  /** Close the note, after a moment for the pointer to reach it unless `immediate`. */
  hide(immediate = false): void {
    if (this.showTimer) {
      clearTimeout(this.showTimer);
      this.showTimer = null;
    }
    this.hold();
    if (immediate) {
      this.active = null;
      this.target = null;
    } else if (this.active) {
      this.hideTimer = setTimeout(() => {
        this.hideTimer = null;
        this.active = null;
        this.target = null;
      }, HIDE_DELAY_MS);
    }
  }

  /** Open the help page behind the note: in the help panel, or typed into the log. */
  async open(): Promise<void> {
    const query = this.active?.tip.help;
    this.hide(true);
    if (!query) return;
    if (settings.helpInPanel) {
      const [{ help }, { dock }] = await Promise.all([import("./help.svelte"), import("./dock.svelte")]);
      // The page first, so the panel opens on it and not on the index.
      await help.open(query);
      dock.openHelp();
    } else {
      const { commands } = await import("./commands.svelte");
      commands.run(`help ${query}`);
    }
  }
}

export const lore = new LoreStore();
