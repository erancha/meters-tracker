import { vi } from "vitest";
import "@testing-library/jest-dom/vitest";
Element.prototype.scrollIntoView = vi.fn();
// jsdom has no object URLs; tests spy on these.
URL.createObjectURL = () => "";
URL.revokeObjectURL = () => {};
window.scrollTo = vi.fn();
// jsdom lays nothing out, so nothing resizes; a test that needs an observer installs its own.
window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
