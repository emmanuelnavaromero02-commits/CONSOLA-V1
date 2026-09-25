"use client";

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

export class DashboardErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="mx-auto max-w-6xl px-6 py-8">
          <div
            role="alert"
            className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
          >
            <p className="font-medium text-destructive">
              No se pudo mostrar el panel.
            </p>
            <button
              type="button"
              onClick={() => this.setState({ hasError: false })}
              className="mt-2 inline-flex min-h-[44px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
            >
              Reintentar
            </button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
