import type { ReactNode } from "react";

import { Badge, Panel, cx } from "@trust/ui";

import type { TraceTone } from "@/lib/types";

export function SyntheticNotice({ compact = false }: { compact?: boolean }) {
  return (
    <div className={cx("synthetic-notice", compact && "synthetic-notice--compact")}>
      <span className="synthetic-notice__icon" aria-hidden="true">
        S
      </span>
      <div>
        <strong>Synthetic environment</strong>
        {!compact ? <span>All apps, people, reservations, and money are fictional.</span> : null}
      </div>
    </div>
  );
}

export function StatusPill({ tone, children }: { tone: TraceTone; children: ReactNode }) {
  return (
    <Badge tone={tone} dot>
      {children}
    </Badge>
  );
}

export function PageIntro({
  eyebrow,
  title,
  description,
  aside,
}: {
  eyebrow: string;
  title: ReactNode;
  description: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div className="page-intro">
      <div className="page-intro__copy">
        <p className="ui-eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {aside ? <div className="page-intro__aside">{aside}</div> : null}
    </div>
  );
}

export function EvidenceRow({
  label,
  value,
  tone = "neutral",
  detail,
}: {
  label: string;
  value: string;
  tone?: TraceTone;
  detail?: string;
}) {
  return (
    <div className="evidence-row">
      <span
        className={cx("evidence-row__marker", `evidence-row__marker--${tone}`)}
        aria-hidden="true"
      />
      <div>
        <span>{label}</span>
        {detail ? <small>{detail}</small> : null}
      </div>
      <strong>{value}</strong>
    </div>
  );
}

export function MetricPendingCard({ label, description }: { label: string; description: string }) {
  return (
    <Panel className="metric-pending-card">
      <div className="metric-pending-card__value" aria-label="No result yet">
        —
      </div>
      <div>
        <strong>{label}</strong>
        <p>{description}</p>
      </div>
      <Badge tone="neutral">Pending</Badge>
    </Panel>
  );
}

export function AppChrome({
  app,
  path,
  children,
  tone = "neutral",
}: {
  app: string;
  path: string;
  children: ReactNode;
  tone?: TraceTone;
}) {
  return (
    <div className="app-chrome">
      <div className="app-chrome__bar">
        <div className="app-chrome__traffic" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div className="app-chrome__origin">
          <span className={cx("app-chrome__lock", `app-chrome__lock--${tone}`)} aria-hidden="true">
            ◇
          </span>
          <span>{app}</span>
          <small>{path}</small>
        </div>
        <Badge tone="neutral" className="app-chrome__synthetic">
          Synthetic
        </Badge>
      </div>
      <div className="app-chrome__viewport">{children}</div>
    </div>
  );
}
