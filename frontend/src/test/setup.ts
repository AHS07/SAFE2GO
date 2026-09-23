import "@testing-library/jest-dom";

// Newer Node versions define their own global localStorage, and the Vitest
// jsdom environment does not replace globals that already exist. Use the
// storage from the jsdom instance so tests behave like a browser.
const dom = (globalThis as unknown as { jsdom: { window: Window } }).jsdom;
for (const name of ["localStorage", "sessionStorage"] as const) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    value: dom.window[name],
  });
}
