// jsdom implements neither ResizeObserver nor matchMedia; antd's
// layout-aware components (Menu, Table, Grid) need both at mount. This
// is the standard antd test stub — minimal no-op observers, never
// asserting on them.
class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver = ResizeObserverStub;

if (typeof globalThis.matchMedia !== "function") {
  globalThis.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
