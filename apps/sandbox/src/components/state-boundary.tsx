import type { ReactNode } from "react";

export function LoadingState({ label = "Loading synthetic state…" }) {
  return (
    <div className="state-panel" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
  );
}

export function ErrorState({ children }: { children: ReactNode }) {
  return (
    <div className="state-panel state-error" role="alert">
      {children}
    </div>
  );
}
