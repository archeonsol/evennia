// Client-side power-user text tools, MUD-style:
//   highlights: colour matching text in the log
//   gags:       hide matching lines entirely
//   aliases:    expand a typed first word into a command
// Rules are structured (pattern + fields), edited row-by-row, persisted locally.

const KEY = "underspire.triggers.v2";

export interface Highlight {
  pattern: string;
  color: string;
}
export interface Gag {
  pattern: string;
}
export interface Alias {
  name: string;
  command: string;
}
export type ActionKind = "sound" | "command" | "notify";
export interface TAction {
  pattern: string;
  kind: ActionKind;
  arg: string; // command to run / notify text; ignored for sound
}

function compile(pattern: string): RegExp | null {
  const p = (pattern || "").trim();
  if (!p) return null;
  try {
    return new RegExp(p, "gi");
  } catch {
    return new RegExp(p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  }
}

class Triggers {
  highlights = $state<Highlight[]>([]);
  gags = $state<Gag[]>([]);
  aliases = $state<Alias[]>([]);
  actions = $state<TAction[]>([]);

  private cHL: { re: RegExp; color: string }[] = [];
  private cGag: RegExp[] = [];
  private cAlias = new Map<string, string>();
  private cAct: { re: RegExp; kind: ActionKind; arg: string }[] = [];

  init(): void {
    try {
      const p = JSON.parse(localStorage.getItem(KEY) || "{}");
      this.highlights = p.highlights ?? [];
      this.gags = p.gags ?? [];
      this.aliases = p.aliases ?? [];
      this.actions = p.actions ?? [];
    } catch {
      /* ignore */
    }
    this.sync();
  }

  /** Recompile matchers + persist. Call after any edit. */
  sync(): void {
    this.cHL = [];
    for (const h of this.highlights) {
      const re = compile(h.pattern);
      if (re) this.cHL.push({ re, color: h.color || "var(--gold)" });
    }
    this.cGag = [];
    for (const g of this.gags) {
      const re = compile(g.pattern);
      if (re) this.cGag.push(re);
    }
    this.cAlias = new Map();
    for (const a of this.aliases) {
      const name = (a.name || "").trim().toLowerCase();
      if (name && a.command.trim()) this.cAlias.set(name, a.command.trim());
    }
    this.cAct = [];
    for (const a of this.actions) {
      const re = compile(a.pattern);
      if (re) this.cAct.push({ re, kind: a.kind, arg: a.arg });
    }
    try {
      localStorage.setItem(
        KEY,
        JSON.stringify({
          highlights: this.highlights,
          gags: this.gags,
          aliases: this.aliases,
          actions: this.actions,
        }),
      );
    } catch {
      /* ignore */
    }
  }

  addHighlight(): void {
    this.highlights = [...this.highlights, { pattern: "", color: "#d4af37" }];
    this.sync();
  }
  removeHighlight(i: number): void {
    this.highlights = this.highlights.filter((_, n) => n !== i);
    this.sync();
  }
  addGag(): void {
    this.gags = [...this.gags, { pattern: "" }];
    this.sync();
  }
  removeGag(i: number): void {
    this.gags = this.gags.filter((_, n) => n !== i);
    this.sync();
  }
  addAlias(): void {
    this.aliases = [...this.aliases, { name: "", command: "" }];
    this.sync();
  }
  removeAlias(i: number): void {
    this.aliases = this.aliases.filter((_, n) => n !== i);
    this.sync();
  }
  addAction(): void {
    this.actions = [...this.actions, { pattern: "", kind: "sound", arg: "" }];
    this.sync();
  }
  removeAction(i: number): void {
    this.actions = this.actions.filter((_, n) => n !== i);
    this.sync();
  }

  /** Fire trigger actions for a line of log text. */
  runActions(text: string): void {
    if (!this.cAct.length) return;
    for (const a of this.cAct) {
      a.re.lastIndex = 0;
      if (!a.re.test(text)) continue;
      if (a.kind === "sound") {
        import("./audio").then((m) => m.playMention());
      } else if (a.kind === "command" && a.arg.trim()) {
        import("./commands.svelte").then((m) => m.commands.run(a.arg));
      } else if (a.kind === "notify") {
        import("./notify.svelte").then((m) => m.notify.ping("Trigger", a.arg || text));
      }
    }
  }

  shouldGag(text: string): boolean {
    if (!this.cGag.length) return false;
    return this.cGag.some((re) => {
      re.lastIndex = 0;
      return re.test(text);
    });
  }

  highlight(html: string): string {
    if (!this.cHL.length) return html;
    let out = html;
    for (const h of this.cHL) {
      out = out.replace(/(<[^>]+>)|([^<]+)/g, (_m, tag, text) => {
        if (tag) return tag;
        h.re.lastIndex = 0;
        return text.replace(h.re, (mm: string) => `<span style="color:${h.color}">${mm}</span>`);
      });
    }
    return out;
  }

  expand(line: string): string {
    if (!this.cAlias.size) return line;
    const sp = line.indexOf(" ");
    const head = (sp < 0 ? line : line.slice(0, sp)).toLowerCase();
    const cmd = this.cAlias.get(head);
    if (!cmd) return line;
    return cmd + (sp < 0 ? "" : line.slice(sp));
  }
}

export const triggers = new Triggers();
