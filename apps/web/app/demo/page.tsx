import type { Metadata } from "next";

import { Badge } from "@trust/ui";

import { PageIntro } from "@/components/product-primitives";
import { ScenarioComposer } from "@/components/scenario-composer";

export const metadata: Metadata = { title: "Disrupted-trip demo" };

export default function DemoPage() {
  return (
    <div className="page-shell">
      <PageIntro
        eyebrow="Interactive task composer"
        title={
          <>
            Break the trip.
            <br />
            Keep the contract.
          </>
        }
        description="Change Maya’s allowed constraints, choose a reproducible failure, and inspect how the protected runtime preserves authority and evidence."
        aside={
          <div>
            <Badge tone="accent">Live-capable console</Badge>
            <p className="page-intro__aside-copy">
              A configured deployment starts the runtime API. Explicitly labeled fixture and
              recorded-replay paths remain available without a model call.
            </p>
          </div>
        }
      />
      <ScenarioComposer />
    </div>
  );
}
