import type { Metadata } from "next";

import { Badge } from "@trust/ui";

import { ApprovalInterface } from "@/components/approval-interface";
import { isFixtureRunId } from "@/lib/runtime-api";

export const metadata: Metadata = { title: "Exact approval scope" };

export default async function ApprovalPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const fixture = isFixtureRunId(runId);
  return (
    <div className="page-shell approval-shell">
      <section className="approval-context">
        <Badge tone={fixture ? "neutral" : "accent"}>
          {fixture ? "Mock approval interface" : "Server-bound approval"}
        </Badge>
        <h1>Approve exactly this rebooking.</h1>
        <p>The scope is narrow, readable, and invalidated by any material context change.</p>
        <ol className="approval-context__steps">
          <li>
            <span>1</span>Review the exact itinerary, traveler, seat, and price.
          </li>
          <li>
            <span>2</span>Confirm every hard constraint remains satisfied.
          </li>
          <li>
            <span>3</span>Approve one paused contractual commit—not a broad instruction.
          </li>
        </ol>
        <Badge tone="warning">
          {fixture
            ? "UI shell only · no server capability"
            : "Capability remains sealed server-side"}
        </Badge>
      </section>
      <ApprovalInterface runId={runId} />
    </div>
  );
}
