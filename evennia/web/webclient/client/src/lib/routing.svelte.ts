// Message routing (old "spawns"): copy log lines matching a pattern into a named
// buffer, shown as tabs in the Spawns panel. A line still appears in the main log
// too - routing duplicates, it doesn't move.

const KEY = "underspire.routing.v1";
const MAX = 300;

export interface Route {
  pattern: string;
  label: string;
}
interface Line {
  html: string;
  ts: number;
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

class Routing {
  routes = $state<Route[]>([]);
  buffers = $state<Record<string, Line[]>>({});
  private cRoutes: { re: RegExp; label: string }[] = [];

  init(): void {
    try {
      this.routes = JSON.parse(localStorage.getItem(KEY) || "[]");
    } catch {
      this.routes = [];
    }
    this.sync();
  }

  sync(): void {
    this.cRoutes = [];
    for (const r of this.routes) {
      const re = compile(r.pattern);
      const label = (r.label || "").trim();
      if (re && label) this.cRoutes.push({ re, label });
    }
    try {
      localStorage.setItem(KEY, JSON.stringify(this.routes));
    } catch {
      /* ignore */
    }
  }

  add(): void {
    this.routes = [...this.routes, { pattern: "", label: "" }];
    this.sync();
  }
  remove(i: number): void {
    this.routes = this.routes.filter((_, n) => n !== i);
    this.sync();
  }

  labels(): string[] {
    return [...new Set(this.cRoutes.map((r) => r.label))];
  }

  /** Copy a line into any buffer whose route matches. */
  process(html: string, text: string): void {
    if (!this.cRoutes.length) return;
    for (const r of this.cRoutes) {
      r.re.lastIndex = 0;
      if (!r.re.test(text)) continue;
      const buf = [...(this.buffers[r.label] ?? []), { html, ts: Date.now() }].slice(-MAX);
      this.buffers = { ...this.buffers, [r.label]: buf };
    }
  }

  clear(label: string): void {
    this.buffers = { ...this.buffers, [label]: [] };
  }
}

export const routing = new Routing();
