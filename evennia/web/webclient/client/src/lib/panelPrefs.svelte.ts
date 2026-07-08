// Per-panel visual overrides (font size + opacity), keyed by dockview panel id.
// Applied by Workspace onto each panel's content element as an inline CSS var /
// opacity, so a panel can be dimmed or sized independently of the global setting.

const KEY = "underspire.panelprefs.v1";

export interface PanelPref {
  fontPx?: number; // overrides --shell-font-size for this panel
  opacity?: number; // 30..100
}

class PanelPrefs {
  prefs = $state<Record<string, PanelPref>>({});

  init(): void {
    try {
      this.prefs = JSON.parse(localStorage.getItem(KEY) || "{}");
    } catch {
      this.prefs = {};
    }
  }

  get(id: string): PanelPref {
    return this.prefs[id] ?? {};
  }

  set(id: string, patch: Partial<PanelPref>): void {
    this.prefs = { ...this.prefs, [id]: { ...this.get(id), ...patch } };
    this.save();
  }

  reset(id: string): void {
    const p = { ...this.prefs };
    delete p[id];
    this.prefs = p;
    this.save();
  }

  private save(): void {
    try {
      localStorage.setItem(KEY, JSON.stringify(this.prefs));
    } catch {
      /* ignore */
    }
  }
}

export const panelPrefs = new PanelPrefs();
