// The help panel's state: the page on show, its history, and search.
//
// Pages come two ways. A typed `help` makes the server push a `help_view` event
// (routed here from main.ts). Everything done inside the panel (a link, the
// search box, back and forward) asks for pages over the `help` RPCs instead, so
// browsing help never prints into the terminal or goes through the parser.

import { connection } from "./evennia.svelte";

export interface HelpTopicStub {
  key: string;
  category: string;
  summary: string;
}

export interface HelpSectionData {
  title: string;
  body: string;
  syntax: string[];
}

export interface HelpTopicData extends HelpTopicStub {
  aliases: string[];
  intro: string;
  sections: HelpSectionData[];
  links: string[];
}

export interface HelpHit {
  query: string;
  key: string;
  category: string;
  section: string | null;
  snippet: string;
}

export interface HelpPage {
  kind: "index" | "category" | "topic" | "section" | "search" | "not_found" | string;
  query: string;
  categories?: { name: string; topics: HelpTopicStub[] }[];
  category?: string;
  topics?: HelpTopicStub[];
  topic?: HelpTopicData;
  section?: string;
  hits?: HelpHit[];
  suggestions?: { label: string; query: string }[];
}

/** Pages kept for instant back/forward. Help is small; this is plenty. */
const CACHE_LIMIT = 60;

class HelpStore {
  page = $state<HelpPage | null>(null);
  results = $state<HelpPage | null>(null);
  loading = $state(false);
  error = $state("");
  history = $state<string[]>([]);
  at = $state(-1);
  /** Bumps on every shown page, so the panel can scroll to its section. */
  shown = $state(0);
  private cache = new Map<string, HelpPage>();

  get canBack(): boolean {
    return this.at > 0;
  }

  get canForward(): boolean {
    return this.at < this.history.length - 1;
  }

  private remember(query: string, page: HelpPage): void {
    this.cache.delete(query);
    this.cache.set(query, page);
    if (this.cache.size > CACHE_LIMIT) {
      const oldest = this.cache.keys().next().value;
      if (oldest !== undefined) this.cache.delete(oldest);
    }
  }

  /** Show a page (from the server push or an RPC) and record it in history. */
  show(page: HelpPage, push = true): void {
    const query = String(page?.query ?? "");
    this.page = page;
    this.error = "";
    this.remember(query, page);
    if (push && this.history[this.at] !== query) {
      this.history = [...this.history.slice(0, this.at + 1), query];
      this.at = this.history.length - 1;
    }
    this.shown += 1;
  }

  /** Open a topic, section, category, or the index (empty query). */
  async open(query: string, push = true): Promise<void> {
    const q = String(query ?? "").trim();
    const cached = this.cache.get(q);
    if (cached && !push) {
      this.show(cached, false);
      return;
    }
    this.loading = true;
    try {
      const page = await connection.request<HelpPage>("help", "help_view", { query: q });
      this.show(page, push);
    } catch (e: any) {
      if (cached) this.show(cached, push);
      else this.error = e?.message ? `Help is unavailable: ${e.message}` : "Help is unavailable.";
    } finally {
      this.loading = false;
    }
  }

  back(): void {
    if (!this.canBack) return;
    this.at -= 1;
    void this.open(this.history[this.at], false);
  }

  forward(): void {
    if (!this.canForward) return;
    this.at += 1;
    void this.open(this.history[this.at], false);
  }

  /** Search as the player types. An empty query clears the results. */
  async search(query: string): Promise<void> {
    const q = String(query ?? "").trim();
    if (!q) {
      this.results = null;
      return;
    }
    try {
      const page = await connection.request<HelpPage>("help", "help_search", { query: q });
      this.results = page;
    } catch {
      this.results = { kind: "search", query: q, hits: [] };
    }
  }

  /** Tell the server whether help goes to this panel or to the log. */
  sendPreference(panel: boolean): void {
    connection.request("help", "help_prefs", { panel }).catch(() => {
      /* offline: sent again on connect */
    });
  }
}

export const help = new HelpStore();
