// jsdom does not implement these, and components that use them would otherwise
// fail for a reason that has nothing to do with the thing under test.

if (!globalThis.matchMedia) {
  globalThis.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof globalThis.matchMedia;
}

if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function scrollTo() {};
}

if (!globalThis.ResizeObserver) {
  // jsdom has no layout, so nothing would ever resize. The virtualized table
  // measures its viewport through one of these; without the stub every test
  // that renders a table fails for a reason unrelated to the table.
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof globalThis.ResizeObserver;
}
