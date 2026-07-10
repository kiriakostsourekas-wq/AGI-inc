import Link from "next/link";
import { formatDateTime } from "@/lib/format";
import { VIRTUAL_NOW } from "@/lib/types";

export default function SandboxIndex() {
  return (
    <main className="index-page">
      <div className="index-label">Synthetic agent evaluation environment</div>
      <h1>Three apps. One disrupted trip. Zero real-world effects.</h1>
      <p className="index-lede">
        This deterministic mini-internet lets a browser agent inspect a cancellation, rebook through
        a guarded synthetic airline, and update a fictional calendar only after confirmation.
      </p>
      <div className="clock-card">
        <span>Scenario clock</span>
        <strong>{formatDateTime(VIRTUAL_NOW)}</strong>
      </div>
      <section className="app-grid" aria-label="Synthetic applications">
        <Link className="index-app-card mail-card" href="/gomail?run=demo">
          <span className="brand-mark">G</span>
          <div>
            <h2>GoMail</h2>
            <p>Cancellation and booking confirmation inbox</p>
          </div>
          <span aria-hidden="true">→</span>
        </Link>
        <Link className="index-app-card air-card" href="/northstar?run=demo">
          <span className="brand-mark">N</span>
          <div>
            <h2>Northstar Air</h2>
            <p>Alternatives, exact approval, and guarded rebooking</p>
          </div>
          <span aria-hidden="true">→</span>
        </Link>
        <Link className="index-app-card day-card" href="/dayplan?run=demo">
          <span className="brand-mark">D</span>
          <div>
            <h2>DayPlan</h2>
            <p>Post-verification travel-block synchronization</p>
          </div>
          <span aria-hidden="true">→</span>
        </Link>
      </section>
      <p className="index-disclaimer">
        All names, records, messages, flights, identities, and prices are fictional test fixtures.
        This environment cannot contact a real provider or spend real money.
      </p>
    </main>
  );
}
