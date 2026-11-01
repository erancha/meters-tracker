import { vi } from "vitest";
import "@testing-library/jest-dom/vitest";
Element.prototype.scrollIntoView = vi.fn();
// jsdom has no object URLs; tests spy on these.
URL.createObjectURL = () => "";
URL.revokeObjectURL = () => {};
