import type { Metadata } from "next";

import { Badge, Panel, SectionHeading } from "@trust/ui";

import { PageIntro } from "@/components/product-primitives";

export const metadata: Metadata = { title: "Methodology and threat model" };

const loop = ["Observe", "Propose", "Authorize", "Act", "Verify", "Recover"];

export default function MethodologyPage() {
  return (
    <div className="page-shell">
      <PageIntro
        eyebrow="Architecture, evaluation, limits"
        title="Trust is a boundary, not a prompt."
        description="The actor is deliberately nondeterministic. Authority, effect accounting, observable verification, and evaluation ground truth are separated around it."
        aside={<Badge tone="neutral">Specification v0.1.0</Badge>}
      />
      <div className="method-loop">
        {loop.map((step, index) => (
          <div className="method-loop__step" key={step}>
            <span>0{index + 1}</span>
            <strong>{step}</strong>
          </div>
        ))}
      </div>

      <section className="method-section">
        <SectionHeading
          eyebrow="Separation of knowledge"
          title="Three views of the same run."
          description="No component gets more visibility than it needs. The oracle scores outcomes but can never choose the actor’s next move."
        />
        <div className="boundary-grid">
          <Panel className="boundary-card">
            <span className="boundary-card__index">01 · ACTOR</span>
            <h3>Rendered pixels only</h3>
            <p>
              A single model adapter decides from screenshots, origin, task contract, plan, belief
              facts, and recent structured summaries.
            </p>
            <ul>
              <li>No DOM or accessibility tree</li>
              <li>No app API or database</li>
              <li>No evaluator or fault metadata</li>
            </ul>
          </Panel>
          <Panel className="boundary-card">
            <span className="boundary-card__index">02 · VERIFIER</span>
            <h3>Observable evidence only</h3>
            <p>
              The runtime verifier checks what a user could see: URL, visible state, confirmation
              message, and issued receipts.
            </p>
            <ul>
              <li>No ground-truth tables</li>
              <li>No hidden expected answer</li>
              <li>No model assertion as proof</li>
            </ul>
          </Panel>
          <Panel className="boundary-card">
            <span className="boundary-card__index">03 · ORACLE</span>
            <h3>Ground truth, scoring only</h3>
            <p>
              A separately credentialed oracle inspects synthetic server state after execution and
              produces reproducible metrics.
            </p>
            <ul>
              <li>Unavailable to actor and verifier</li>
              <li>Never influences next action</li>
              <li>Links aggregates to raw runs</li>
            </ul>
          </Panel>
        </div>
      </section>

      <section className="method-section">
        <SectionHeading
          eyebrow="Exact authority"
          title="Approval is bound to the action that spends it."
          description="A browser card alone cannot grant permission. The target runtime mints and consumes a server-side capability at the normal rendered commit boundary."
        />
        <div className="approval-flow">
          {[
            ["01", "User approves", "Exact itinerary, seat, traveler, price, and currency"],
            ["02", "Runtime binds", "Contract, observation, context, nonce, expiry, idempotency"],
            ["03", "Actor clicks", "Northstar’s visible confirmation control"],
            ["04", "Gateway validates", "Exact match and atomic single-use consumption"],
            ["05", "Verifier proves", "Manage Trip plus matching confirmation email"],
          ].map(([n, t, c]) => (
            <div className="approval-flow__node" key={n}>
              <span>{n}</span>
              <strong>{t}</strong>
              <small>{c}</small>
            </div>
          ))}
        </div>
      </section>

      <section className="method-section">
        <SectionHeading
          eyebrow="Threat model"
          title="The failure modes are part of the interface."
          description="Safety behavior should be visible, testable, and reproducible—not buried in a system prompt."
        />
        <div className="threat-grid">
          <Panel className="threat-card">
            <h3>Authority and content</h3>
            <ul>
              <li>Prompt injection in email and page content</li>
              <li>Confused-deputy and scope-widening attacks</li>
              <li>Approval replay, mutation, and expiry</li>
              <li>Credential or identity leakage</li>
            </ul>
          </Panel>
          <Panel className="threat-card">
            <h3>Execution and evidence</h3>
            <ul>
              <li>Duplicate side effects after ambiguous responses</li>
              <li>Stale prices and itinerary changes</li>
              <li>Oracle contamination of actor context</li>
              <li>Tampered or cherry-picked artifacts</li>
            </ul>
          </Panel>
        </div>
      </section>

      <section className="method-section">
        <SectionHeading
          eyebrow="Paired evaluation"
          title="The runtime earns its delta."
          description="Each fault seed runs once without the trust components and once with them, while model, inputs, screenshots, tools, budgets, and initial state remain fixed."
        />
        <div className="limits-table">
          <div className="limits-table__row">
            <strong>Primary comparison</strong>
            <span>30 seeds × baseline/protected = 60 planned executions</span>
          </div>
          <div className="limits-table__row">
            <strong>Primary metric</strong>
            <span>
              Safe-task success: correct outcome, all predicates, zero severe violations, zero
              unauthorized or duplicate effects
            </span>
          </div>
          <div className="limits-table__row">
            <strong>Failure disclosure</strong>
            <span>
              No failed result is discarded; infrastructure-invalid attempts remain in the raw table
            </span>
          </div>
          <div className="limits-table__row">
            <strong>Public proof</strong>
            <span>
              Git SHA, model, prompt, browser, seeds, hashes, raw JSON/CSV, and trace links
            </span>
          </div>
        </div>
      </section>

      <section className="method-section">
        <SectionHeading
          eyebrow="Honest limits"
          title="What this MVP does not claim."
          description="A narrow reference system is easier to inspect and harder to bluff."
        />
        <div className="limits-table">
          <div className="limits-table__row">
            <strong>No production claim</strong>
            <span>
              The sandbox demonstrates boundaries; it is not a security certification or production
              travel system.
            </span>
          </div>
          <div className="limits-table__row">
            <strong>No real integrations</strong>
            <span>
              GoMail, Northstar Air, DayPlan, identities, reservations, credentials, and money are
              synthetic.
            </span>
          </div>
          <div className="limits-table__row">
            <strong>No general autonomy</strong>
            <span>
              One task family, one actor, bounded tools, bounded origins, and bounded budgets.
            </span>
          </div>
          <div className="limits-table__row">
            <strong>No on-device claim</strong>
            <span>The MVP does not include native mobile control or local model inference.</span>
          </div>
        </div>
      </section>
    </div>
  );
}
