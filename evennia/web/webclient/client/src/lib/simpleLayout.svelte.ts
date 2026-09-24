// The screen-reader layout: one view at a time, full width, switched from a
// tab list. The docked workspace puts the log, scene and channels side by side
// and speaks all three; players reading by ear asked for less at once.
//
// `dock` routes its open calls here while screen-reader mode is on, so every
// caller (the VIEWS menu, the server's web_panel, channel pop-outs) works the
// same in either layout.

export interface SimpleView {
  id: string;
  component: string;
  title: string;
  params?: Record<string, unknown>;
  /** The base three stay; anything opened later can be closed again. */
  closable: boolean;
}

const BASE: SimpleView[] = [
  { id: "log", component: "log", title: "Terminal", closable: false },
  { id: "scene", component: "scene", title: "Scene", closable: false },
  { id: "chat", component: "chat", title: "Channels", closable: false },
];

class SimpleLayout {
  views = $state<SimpleView[]>(BASE.map((v) => ({ ...v })));
  active = $state("log");

  has(id: string): boolean {
    return this.views.some((v) => v.id === id);
  }

  /** Add a view if it is new, then show it. */
  open(view: Omit<SimpleView, "closable"> & { closable?: boolean }): void {
    if (!this.has(view.id)) this.views = [...this.views, { closable: true, ...view }];
    this.active = view.id;
  }

  show(id: string): void {
    if (this.has(id)) this.active = id;
  }

  close(id: string): void {
    const view = this.views.find((v) => v.id === id);
    if (!view?.closable) return;
    this.views = this.views.filter((v) => v.id !== id);
    if (this.active === id) this.active = "log";
  }
}

export const simple = new SimpleLayout();
