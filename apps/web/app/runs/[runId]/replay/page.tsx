import type { Metadata } from "next";

import { Badge } from "@trust/ui";

import { PageIntro } from "@/components/product-primitives";
import { ReplayPlayer } from "@/components/replay-player";
import { isFixtureRunId } from "@/lib/runtime-api";

export const metadata: Metadata = { title: "Deterministic replay" };

export default async function ReplayPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const fixture = isFixtureRunId(runId);
  return (
    <div className="page-shell">
      <PageIntro
        eyebrow="Inspectable artifact"
        title="Replay the evidence, not the model."
        description={
          fixture
            ? "Step through a synchronized fixture showing approval, an ambiguous 504, blocked retry, external verification, and a calendar update."
            : "Step through immutable browser and trace artifacts fetched from the completed runtime run."
        }
        aside={
          <div>
            <Badge tone="accent">
              {fixture ? "Fixture replay · no model calls" : "Recorded replay · no model calls"}
            </Badge>
            <p className="page-intro__aside-copy">
              {fixture
                ? "This shell is backed by clearly labeled committed fixture data."
                : "This page reads stored artifacts only and never restarts or imitates a live actor."}
            </p>
          </div>
        }
      />
      <ReplayPlayer runId={runId} />
    </div>
  );
}
