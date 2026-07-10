import Link from "next/link";
import type { ReactNode } from "react";
import { formatDateTime } from "@/lib/format";
import { VIRTUAL_NOW } from "@/lib/types";

type AppShellProps = {
  app: "gomail" | "northstar" | "dayplan";
  runId: string;
  children: ReactNode;
};

const appLabels = {
  gomail: "GoMail",
  northstar: "Northstar Air",
  dayplan: "DayPlan",
};

export function AppShell({ app, runId, children }: AppShellProps) {
  const query = `?run=${encodeURIComponent(runId)}`;
  return (
    <div className={`app-shell app-${app}`} data-sandbox-app={app}>
      <div className="synthetic-ribbon" role="note">
        <span>Synthetic demo environment</span>
        <span>·</span>
        <span>No real accounts, travel, or money</span>
      </div>
      <header className="app-header">
        <Link className="app-brand" href={`/${app}${query}`}>
          <span className="brand-mark" aria-hidden="true">
            {app === "gomail" ? "G" : app === "northstar" ? "N" : "D"}
          </span>
          <span>{appLabels[app]}</span>
          <span className="synthetic-pill">Synthetic</span>
        </Link>
        <div className="virtual-clock" title="Deterministic scenario clock">
          <span className="clock-dot" aria-hidden="true" />
          Virtual time · {formatDateTime(VIRTUAL_NOW)}
        </div>
      </header>
      <nav className="app-switcher" aria-label="Synthetic applications">
        <Link data-active={app === "gomail"} href={`/gomail${query}`}>
          GoMail
        </Link>
        <Link data-active={app === "northstar"} href={`/northstar${query}`}>
          Northstar Air
        </Link>
        <Link data-active={app === "dayplan"} href={`/dayplan${query}`}>
          DayPlan
        </Link>
        <span className="run-chip">Run · {runId}</span>
      </nav>
      <main className="app-main">{children}</main>
    </div>
  );
}
