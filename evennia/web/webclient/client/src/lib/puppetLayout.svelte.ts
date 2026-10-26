// Which puppet terminals are shown in the Puppets panel, and how they are laid
// out. Kept per account, like the command history, so the next person at a
// shared browser does not inherit a staff member's open terminals.

import { type PuppetArrange, parseSaved, shownIds, toggled } from "./puppetLayout";

export const PUPPET_LAYOUT_KEY = "underspire.puppets.layout";

class PuppetLayout {
  /** The terminals the player chose to show, in order; null until they choose, which shows every puppet. */
  open = $state<number[] | null>(null);
  arrange = $state<PuppetArrange>("grid");
  /** One terminal given the whole panel. Not kept: it is a glance, not a layout. */
  solo = $state<number | null>(null);
  private account: number | null = null;
  /** Puppets already on the roster, so only a new arrival is opened for the player. */
  private seen = new Set<number>();

  /** Load the layout of the account the shell is signed in as; null forgets it. */
  useAccount(account: number | null): void {
    if (account === this.account) return;
    this.account = account;
    this.open = null;
    this.arrange = "grid";
    this.solo = null;
    this.seen = new Set();
    if (account == null) return;
    try {
      const saved = parseSaved(localStorage.getItem(`${PUPPET_LAYOUT_KEY}:${account}`));
      this.open = saved.open;
      this.arrange = saved.arrange;
    } catch {
      /* ignore */
    }
  }

  /** The ids of the terminals on screen, given the roster. */
  shown(known: readonly number[]): number[] {
    if (this.solo != null && known.includes(this.solo)) return [this.solo];
    return shownIds(this.open, known);
  }

  /**
   * The roster changed. A puppet that was not there before is opened beside
   * the rest, since the player added it to work with it; the first roster is
   * only noted, so a saved choice to hide a puppet is respected.
   */
  sync(known: readonly number[]): void {
    const first = this.seen.size === 0;
    const fresh = known.filter((id) => !this.seen.has(id));
    for (const id of fresh) this.seen.add(id);
    if (!first && this.open !== null && fresh.length) {
      this.open = [...this.open, ...fresh];
      this.save();
    }
    if (this.solo != null && !known.includes(this.solo)) this.solo = null;
  }

  toggle(id: number, known: readonly number[]): void {
    this.open = toggled(shownIds(this.open, known), id);
    if (this.solo != null && this.solo !== id) this.solo = null;
    this.save();
  }

  setArrange(arrange: PuppetArrange): void {
    this.arrange = arrange;
    this.solo = null;
    this.save();
  }

  showAll(known: readonly number[]): void {
    this.open = [...known];
    this.solo = null;
    this.save();
  }

  hideAll(): void {
    this.open = [];
    this.solo = null;
    this.save();
  }

  toggleSolo(id: number): void {
    this.solo = this.solo === id ? null : id;
  }

  private save(): void {
    if (this.account == null) return;
    try {
      localStorage.setItem(
        `${PUPPET_LAYOUT_KEY}:${this.account}`,
        JSON.stringify({ open: this.open, arrange: this.arrange }),
      );
    } catch {
      /* ignore */
    }
  }
}

export const puppetLayout = new PuppetLayout();
