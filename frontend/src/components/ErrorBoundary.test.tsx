import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { texts } from "../texts";
import { ErrorBoundary } from "./ErrorBoundary";

function Bomb(): never {
  throw new Error("render crash");
}

describe("ErrorBoundary", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders its children when nothing throws", () => {
    render(
      <ErrorBoundary>
        <p>תוכן</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("תוכן")).toBeInTheDocument();
  });

  it("shows the fallback when a descendant crashes during render", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    expect(screen.getByText(texts.crashed)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: texts.backToStart })).toHaveAttribute("href", "/");
  });
});
