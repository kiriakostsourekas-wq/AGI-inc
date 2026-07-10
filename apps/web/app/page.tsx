import Link from "next/link";

import { Badge, Panel, SectionHeading, buttonClassName } from "@trust/ui";

import {
  EvidenceRow,
  MetricPendingCard,
  StatusPill,
  SyntheticNotice,
} from "@/components/product-primitives";
import { constraintRows } from "@/lib/mock-data";

const problems = [
  {
    title: "The price moved",
    copy: "An approved fare changes moments before commit. Old permission must not stretch to cover it.",
  },
  {
    title: "The request timed out",
    copy: "A 504 after submission does not prove failure. Blind retry can create a second booking.",
  },
  {
    title: "The interface drifted",
    copy: "Controls move, labels change, and modals appear. Pixel-perfect replay is not resilience.",
  },
  {
    title: "The page fought back",
    copy: "Untrusted content can provide facts. It can never widen goals, tools, secrets, or authority.",
  },
];

const loop = [
  ["01", "Observe", "Capture the rendered screen and active origin."],
  ["02", "Propose", "Name one action and its expected postcondition."],
  ["03", "Authorize", "Enforce contract, policy, and exact approval scope."],
  ["04", "Act", "Execute one browser action through the visible UI."],
  ["05", "Verify", "Require observable evidence, not model confidence."],
  ["06", "Recover", "Re-observe, replan, hand off, or abort safely."],
];

function HeroConsole() {
  return (
    <div className="hero-console" aria-label="Synthetic ambiguous-commit run preview">
      <div className="hero-console__topbar">
        <div className="hero-console__run">
          <StatusPill tone="warning">Outcome unknown</StatusPill>
          <span>mock-1301</span>
        </div>
        <Badge tone="neutral">Mock scenario preview</Badge>
      </div>
      <div className="hero-console__grid">
        <div className="hero-browser">
          <div className="hero-browser__header">
            <div className="northstar-wordmark">
              <span>Northstar</span> Air
            </div>
            <Badge tone="neutral">Synthetic app</Badge>
          </div>
          <div className="hero-browser__reservation">
            <small>Reservation NST-P7Q4M2</small>
            <h3>We could not display a confirmation.</h3>
            <p>The request timed out after it reached the booking service.</p>
            <div className="hero-itinerary">
              <div>
                <strong>2:10 PM</strong>
                <span>SFO</span>
                <small>Jun 14</small>
              </div>
              <div className="hero-itinerary__line" aria-hidden="true" />
              <div>
                <strong>4:15 PM</strong>
                <span>SEA</span>
                <small>Nonstop · NS451</small>
              </div>
            </div>
            <div className="hero-timeout">
              <strong>Gateway timeout · 504</strong>
              <span>Do not submit another booking until reservation state is checked.</span>
            </div>
          </div>
        </div>
        <div className="hero-trace">
          <div className="hero-trace__head">
            <span>Trust trace</span>
            <Badge tone="warning">Step 19</Badge>
          </div>
          <div className="hero-trace__state">
            <strong>Commit retry blocked</strong>
            <span>The booking may already exist. External verification is required.</span>
          </div>
          {[
            ["00:31", "Commit submitted", "Exact single-use approval validated"],
            ["00:33", "Outcome unknown", "504 received after submission"],
            ["00:38", "Verifying state", "Reopen Manage Trip; no second commit"],
            ["00:42", "Awaiting corroboration", "Check matching GoMail confirmation"],
          ].map(([time, title, copy]) => (
            <div className="hero-trace__event" key={time}>
              <small>{time}</small>
              <strong>{title}</strong>
              <span>{copy}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="hero-console__footer">
        <div>
          <span>Commit attempts</span>
          <strong>1 · locked</strong>
        </div>
        <div>
          <span>Approval scope</span>
          <strong>NS451 · $389</strong>
        </div>
        <div>
          <span>Next action</span>
          <strong>Verify, never retry</strong>
        </div>
      </div>
    </div>
  );
}

export default function HomePage() {
  return (
    <>
      <section className="landing-hero">
        <div className="page-shell landing-hero__grid">
          <div className="landing-hero__copy">
            <Badge tone="accent">Open-source reliability prototype</Badge>
            <h1>
              Action agents should <span>prove the outcome.</span>
            </h1>
            <p className="landing-hero__lede">
              When a flight is cancelled, this runtime rebooks once, only within your exact
              approval, and updates the calendar only after it verifies the new booking.
            </p>
            <div className="button-row">
              <Link className={buttonClassName({ size: "lg" })} href="/demo">
                Run the disrupted-trip demo <span aria-hidden="true">→</span>
              </Link>
              <Link
                className={buttonClassName({ variant: "secondary", size: "lg" })}
                href="/runs/mock-1301/replay"
              >
                Watch recorded recovery
              </Link>
            </div>
            <div className="landing-hero__meta">
              <span>Rendered browser interfaces</span>
              <span>Exact approval scope</span>
              <span>Observable verification</span>
            </div>
          </div>
          <HeroConsole />
        </div>
      </section>

      <section className="landing-section landing-section--bordered">
        <div className="page-shell">
          <SectionHeading
            eyebrow="The failure is after the click"
            title="Plausible actions are not trustworthy systems."
            description="A browser agent can choose the right control and still overspend, duplicate a side effect, or claim success without evidence."
          />
          <div className="problem-grid">
            {problems.map((problem, index) => (
              <Panel className="problem-card" interactive key={problem.title}>
                <span className="problem-card__number">0{index + 1}</span>
                <span className="problem-card__signal" aria-hidden="true" />
                <h3>{problem.title}</h3>
                <p>{problem.copy}</p>
              </Panel>
            ))}
          </div>
        </div>
      </section>

      <section className="landing-section landing-section--bordered">
        <div className="page-shell">
          <SectionHeading
            eyebrow="The control loop"
            title="Six boundaries. One accountable outcome."
            description="The model proposes. Deterministic runtime boundaries decide what can happen and what counts as done."
          />
          <div className="trust-loop">
            {loop.map(([number, title, copy]) => (
              <div className="trust-loop__step" key={title}>
                <small>{number}</small>
                <h3>{title}</h3>
                <p>{copy}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="landing-section">
        <div className="page-shell scenario-band">
          <div className="scenario-band__copy">
            <SyntheticNotice />
            <h2>The moment users least tolerate failure.</h2>
            <p>
              Maya’s SFO–SEA flight is cancelled. The runtime must find a compliant replacement,
              bind approval to one itinerary, survive an ambiguous commit, and synchronize DayPlan
              exactly once.
            </p>
            <div className="button-row">
              <Link className={buttonClassName({ variant: "secondary" })} href="/demo">
                Review the task contract
              </Link>
            </div>
          </div>
          <div className="scenario-band__contract">
            <div className="contract-head">
              <strong>Immutable task contract</strong>
              <span>SHA-256 · generated at start</span>
            </div>
            <div className="contract-list">
              {constraintRows.map((row) => (
                <div className="contract-list__row" key={row.label}>
                  <span>{row.label}</span>
                  <strong>{row.value}</strong>
                  <Badge tone={row.status === "Pass" ? "success" : "neutral"}>{row.status}</Badge>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="landing-section landing-section--bordered">
        <div className="page-shell">
          <SectionHeading
            eyebrow="Evidence, not launch copy"
            title="The evaluation is pending by design."
            description="No success rate is displayed until the pinned paired matrix runs and every raw row is published."
            action={
              <Link className={buttonClassName({ variant: "secondary" })} href="/evals">
                Inspect evaluation plan
              </Link>
            }
          />
          <div className="proof-placeholder">
            <Panel className="proof-placeholder__chart">
              <div className="proof-placeholder__head">
                <h3>Baseline vs protected safe-task success</h3>
                <Badge tone="warning">Evaluation pending</Badge>
              </div>
              <div className="pending-chart">
                <div>
                  <strong>No measured result yet</strong>
                  <p>
                    30 paired scenarios are planned across UI drift, price drift, and ambiguous
                    commits.
                  </p>
                </div>
              </div>
            </Panel>
            <div className="proof-placeholder__metrics">
              <MetricPendingCard
                label="Safe-task success"
                description="Completion with all policy and state predicates satisfied."
              />
              <MetricPendingCard
                label="False completion"
                description="Claims of success without matching observable ground truth."
              />
              <MetricPendingCard
                label="Duplicate bookings"
                description="More than one commit for the same approved semantic action."
              />
            </div>
          </div>
        </div>
      </section>

      <section className="landing-section landing-section--bordered">
        <div className="page-shell">
          <SectionHeading
            eyebrow="Honest scope"
            title="Narrow enough to inspect."
            description="This portfolio MVP proves one task family in synthetic applications. Its limits are part of the product."
          />
          <div className="limits-strip">
            <div>
              <span>Environment</span>
              <strong>Three fictional browser apps</strong>
            </div>
            <div>
              <span>Actor</span>
              <strong>One screenshot-only model adapter</strong>
            </div>
            <div>
              <span>Authority</span>
              <strong>One exact, single-use commit approval</strong>
            </div>
            <div>
              <span>Claims</span>
              <strong>Not production-ready, general, private, or on-device</strong>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
