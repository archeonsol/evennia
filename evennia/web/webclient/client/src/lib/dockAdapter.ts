// Bridge dockview-core (imperative, framework-agnostic) to Svelte 5. dockview
// asks for a content renderer per panel; we mount a Svelte component into the
// panel's element and unmount it on dispose. `params.params` from addPanel is
// passed through as props.
//
// This file *is* the Svelte binding, which is why we depend on `dockview-core`
// rather than the `dockview` package: that one is the React binding, and
// `dockview-vue` is Vue's. Core is the supported path for every other framework
// — its own description is "for vanilla TypeScript".
//
// dockview-core warns once on the console when nothing has claimed to be a
// binding, to catch apps reaching into internals by mistake. That is what
// `markDockviewPackageLoaded` is for, and we are exactly the case it exempts.
// (Its `NODE_ENV === "production"` escape hatch never fires here: the shell is
// an IIFE browser bundle with no `process` shim, so the warning reached players.)

import { mount, unmount } from "svelte";
import { markDockviewPackageLoaded } from "dockview-core";
import type { IContentRenderer, GroupPanelPartInitParameters } from "dockview-core";

markDockviewPackageLoaded();

class SveltePanel implements IContentRenderer {
  readonly element: HTMLElement;
  private instance: any = null;

  constructor(private Component: any) {
    this.element = document.createElement("div");
    this.element.style.height = "100%";
    this.element.style.width = "100%";
    this.element.style.overflow = "hidden";
  }

  init(params: GroupPanelPartInitParameters): void {
    this.instance = mount(this.Component, {
      target: this.element,
      props: (params.params ?? {}) as Record<string, unknown>,
    });
  }

  dispose(): void {
    if (this.instance) {
      unmount(this.instance);
      this.instance = null;
    }
  }
}

/** Build dockview's `createComponent` from a name→SvelteComponent registry. */
export function svelteComponents(registry: Record<string, any>) {
  return (options: { name: string }): IContentRenderer => {
    const Component = registry[options.name];
    if (!Component) throw new Error(`dockview: no panel component "${options.name}"`);
    return new SveltePanel(Component);
  };
}
