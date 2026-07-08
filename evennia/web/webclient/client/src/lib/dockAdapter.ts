// Bridge dockview-core (imperative, framework-agnostic) to Svelte 5. dockview
// asks for a content renderer per panel; we mount a Svelte component into the
// panel's element and unmount it on dispose. `params.params` from addPanel is
// passed through as props.

import { mount, unmount } from "svelte";
import type { IContentRenderer, GroupPanelPartInitParameters } from "dockview-core";

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
