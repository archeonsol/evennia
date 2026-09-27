// Server-sourced completion data for Tab. The verb lexicon (reachable verbs +
// !social tokens) is pushed once per connect; scope-target names (room, carried
// inventory) are fetched on demand because they change constantly. Matching and
// cycling stay in the command input, which keeps Tab latency-free.

import { connection } from "./evennia.svelte";

/** Scope answers older than this are refetched: a room turns over fast. */
const SCOPE_TTL_MS = 5000;

export type Requester = (ns: string, action: string, data?: any) => Promise<any>;

export class CompletionLexicon {
  verbs = $state<string[]>([]);

  private scope = new Map<string, { at: number; names: string[] }>();
  private inflight = new Map<string, Promise<string[]>>();
  private request: Requester;

  constructor(request?: Requester) {
    this.request = request ?? ((ns, action, data) => connection.request(ns, action, data));
  }

  setVerbs(verbs: unknown): void {
    this.verbs = Array.isArray(verbs) ? verbs.filter((v): v is string => typeof v === "string") : [];
  }

  /** Cached scope names for a partial word, or undefined when never fetched. */
  scopeMatches(word: string): string[] | undefined {
    const hit = this.scope.get(word);
    return hit && Date.now() - hit.at < SCOPE_TTL_MS ? hit.names : undefined;
  }

  /** Scope names matching ``word``; one request per word, failures cache empty. */
  fetchScope(word: string): Promise<string[]> {
    const fresh = this.scopeMatches(word);
    if (fresh) return Promise.resolve(fresh);
    let p = this.inflight.get(word);
    if (!p) {
      p = this.request("client", "complete", { word })
        .then((reply) => {
          const names = Array.isArray(reply?.names) ? reply.names : [];
          this.scope.set(word, { at: Date.now(), names });
          return names;
        })
        .catch(() => {
          // Offline or refused: cache empty briefly so Tab does not re-fire a
          // doomed request on every press.
          this.scope.set(word, { at: Date.now(), names: [] });
          return [];
        })
        .finally(() => this.inflight.delete(word));
      this.inflight.set(word, p);
    }
    return p;
  }

  /** A room change invalidates every scope answer. */
  reset(): void {
    this.scope.clear();
  }
}

export const lexicon = new CompletionLexicon();
