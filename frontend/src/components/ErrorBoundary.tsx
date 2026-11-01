import { Component, type ReactNode } from "react";
import { texts } from "../texts";

/**
 * Last-resort catch for render-time crashes anywhere below it: shows a refresh prompt instead
 * of the blank page React leaves after an uncaught render error.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { crashed: boolean }> {
  state = { crashed: false };

  static getDerivedStateFromError() {
    return { crashed: true };
  }

  render() {
    if (this.state.crashed) {
      return (
        <main>
          <div className="alert">{texts.crashed} <a href="/">{texts.backToStart}</a></div>
        </main>
      );
    }
    return this.props.children;
  }
}
