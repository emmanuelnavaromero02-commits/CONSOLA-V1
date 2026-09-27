import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

export interface Mounted {
  container: HTMLDivElement;
  render: (node: ReactNode) => Promise<void>;
  unmount: () => Promise<void>;
}

export function mount(): Mounted {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.append(container);
  const root: Root = createRoot(container);
  return {
    container,
    render: async (node) => {
      await act(async () => {
        root.render(node);
      });
    },
    unmount: async () => {
      await act(async () => root.unmount());
      container.remove();
    },
  };
}
