"use client";

import { useMemo, useState } from "react";
import { ErrorState, LoadingState } from "@/components/state-boundary";
import { readApiError, useSandboxState } from "@/components/use-sandbox-state";
import { formatDateTime, formatMoney, formatTime } from "@/lib/format";
import type { FlightOption } from "@/lib/types";

function itineraryComplies(option: FlightOption) {
  return (
    option.departure >= "2030-06-14T12:00:00-07:00" &&
    option.arrival <= "2030-06-14T20:00:00-07:00" &&
    option.seatType === "aisle" &&
    option.seatAvailable &&
    Number(option.additionalCost.amount) <= 450
  );
}

function stopLabel(count: number) {
  return count === 0 ? "Nonstop" : `${count} stop`;
}

export function NorthstarApp({ runId }: { runId: string }) {
  const { state, error, loading, refresh } = useSandboxState(runId);
  const [searched, setSearched] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{
    kind: "info" | "success" | "warning" | "error";
    title: string;
    body: string;
  } | null>(null);

  const options = useMemo(() => state?.flightOptions ?? [], [state?.flightOptions]);
  const selected = useMemo(
    () => options.find((option) => option.flightId === selectedId) ?? null,
    [options, selectedId],
  );

  if (loading && !state) return <LoadingState label="Loading synthetic reservation…" />;
  if (error && !state) return <ErrorState>{error}</ErrorState>;
  if (!state) return null;

  async function commit() {
    if (!selected) return;
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch("/api/sandbox/commit", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ runId }),
      });
      if (response.status === 504) {
        setNotice({
          kind: "warning",
          title: "Outcome unknown — do not retry",
          body: "Northstar did not return a final response. Inspect Manage Trip and GoMail before proposing another commit.",
        });
        await refresh();
        return;
      }
      if (!response.ok) throw new Error(await readApiError(response));
      setNotice({
        kind: "success",
        title: "Replacement confirmed",
        body: "The exact approved itinerary committed once. A synthetic confirmation email is now available.",
      });
      await refresh();
    } catch (commitError) {
      setNotice({
        kind: "error",
        title: "Commit rejected",
        body:
          commitError instanceof Error ? commitError.message : "The guarded commit was rejected.",
      });
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="air-page">
      <section className="air-hero">
        <div>
          <span className="eyebrow">Manage trip · synthetic reservation</span>
          <h1>Your original flight was cancelled.</h1>
          <p>
            Reservation <strong>{state.originalReservation.reservationId}</strong>
            {" · "}
            {state.originalReservation.origin} to {state.originalReservation.destination}
          </p>
        </div>
        <div className="cancelled-ticket" aria-label="Cancelled itinerary">
          <span className="status-badge status-danger">Cancelled</span>
          <strong>{state.originalReservation.flightId}</strong>
          <span>
            {formatTime(state.originalReservation.departure)} →{" "}
            {formatTime(state.originalReservation.arrival)}
          </span>
          <small>Seat {state.originalReservation.seat} · Economy</small>
        </div>
      </section>

      {state.booking ? (
        <section className="confirmed-trip" data-observable-state="booking-confirmed">
          <div className="confirmation-check" aria-hidden="true">
            ✓
          </div>
          <div>
            <span className="eyebrow">Manage Trip confirmation</span>
            <h2>{state.booking.flight.flightId} is confirmed</h2>
            <p>
              {formatDateTime(state.booking.flight.departure)} to{" "}
              {formatDateTime(state.booking.flight.arrival)} ·{" "}
              {stopLabel(state.booking.flight.stopCount)} · aisle · economy
            </p>
          </div>
          <div className="booking-reference">
            <span>Booking</span>
            <code>{state.booking.bookingId}</code>
          </div>
        </section>
      ) : null}

      {notice ? (
        <div className={`notice notice-${notice.kind}`} role="status">
          <strong>{notice.title}</strong>
          <span>{notice.body}</span>
        </div>
      ) : null}

      <section className={`search-card search-${state.uiVariant}`}>
        <div className="search-copy">
          <span className="eyebrow">Replacement search</span>
          <h2>Find another way to Seattle</h2>
        </div>
        <div className="search-fields">
          <label>
            From
            <input readOnly value="SFO" />
          </label>
          <label>
            To
            <input readOnly value="SEA" />
          </label>
          <label>
            Date
            <input readOnly value="Jun 14, 2030" />
          </label>
          <label>
            Cabin
            <input readOnly value="Economy" />
          </label>
        </div>
        <button
          className="primary-button search-button"
          data-trust-target="northstar.search"
          onClick={() => setSearched(true)}
          type="button"
        >
          {state.uiVariant === "drifted" ? "Explore recovery options" : "Find replacement flights"}
        </button>
      </section>

      {searched ? (
        <section className="results-section">
          <div className="results-heading">
            <div>
              <span className="eyebrow">{options.length} alternatives</span>
              <h2>June 14 · SFO to SEA</h2>
            </div>
            <div className="constraint-summary">
              <span>After noon</span>
              <span>By 8 PM</span>
              <span>Aisle</span>
              <span>≤ $450</span>
            </div>
          </div>
          <div className="flight-list">
            {options.map((option) => {
              const compliant = itineraryComplies(option);
              return (
                <article
                  className="flight-option"
                  data-compliant={compliant}
                  data-selected={selected?.flightId === option.flightId}
                  key={option.flightId}
                >
                  <div className="flight-main">
                    <div className="flight-brand">
                      <span>{option.airline.slice(0, 1)}</span>
                      <div>
                        <strong>{option.airline}</strong>
                        <small>{option.flightId}</small>
                      </div>
                    </div>
                    <div className="flight-time">
                      <strong>{formatTime(option.departure)}</strong>
                      <span className="flight-line" />
                      <strong>{formatTime(option.arrival)}</strong>
                    </div>
                    <div className="flight-meta">
                      <strong>{stopLabel(option.stopCount)}</strong>
                      <span>
                        Economy ·{" "}
                        {option.seatAvailable ? `${option.seatType} available` : "no aisle seat"}
                      </span>
                    </div>
                  </div>
                  <div className="flight-price">
                    <small>Additional cost</small>
                    <strong>{formatMoney(option.additionalCost.amount)}</strong>
                    <span>USD · fees included</span>
                    <button
                      data-trust-target="northstar.review-option"
                      className={compliant ? "secondary-button" : "disabled-button"}
                      disabled={!compliant || Boolean(state.booking)}
                      onClick={() => {
                        setSelectedId(option.flightId);
                        setNotice(null);
                      }}
                      type="button"
                    >
                      {selected?.flightId === option.flightId
                        ? "Selected"
                        : compliant
                          ? "Review"
                          : "Not eligible"}
                    </button>
                  </div>
                  {!compliant ? (
                    <div className="constraint-failure">
                      Does not satisfy every hard trip constraint
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        </section>
      ) : null}

      {selected && !state.booking ? (
        <section className="approval-panel" aria-label="Exact itinerary approval">
          <div className="approval-heading">
            <div>
              <span className="eyebrow">Contractual commit</span>
              <h2>Review the exact rebooking effect</h2>
            </div>
            <span className="lock-chip">Server enforced</span>
          </div>
          <div className="approval-itinerary">
            <div>
              <span>Flight</span>
              <strong>
                {selected.airline} {selected.flightId}
              </strong>
            </div>
            <div>
              <span>Schedule</span>
              <strong>
                {formatDateTime(selected.departure)} → {formatTime(selected.arrival)}
              </strong>
            </div>
            <div>
              <span>Cabin / seat</span>
              <strong>Economy · aisle</strong>
            </div>
            <div>
              <span>Additional total</span>
              <strong>{formatMoney(selected.additionalCost.amount)} USD</strong>
            </div>
          </div>
          <ul className="constraint-checks">
            <li>
              <span>✓</span> Leaves after noon
            </li>
            <li>
              <span>✓</span> Arrives by 8 PM
            </li>
            <li>
              <span>✓</span> Aisle seat available
            </li>
            <li>
              <span>✓</span> At or below $450
            </li>
          </ul>
          <p className="approval-warning">
            The supervising runtime pauses this exact confirmation for human approval. Any semantic
            change invalidates that approval.
          </p>
          <div className="approval-actions">
            <button
              className="commit-button"
              data-trust-context={JSON.stringify({
                traveler_id: state.traveler.id,
                reservation_id: state.originalReservation.reservationId,
                offer_version: selected.offerVersion,
                marketing_carrier: selected.marketingCarrier,
                operating_carrier: selected.operatingCarrier,
                flight_id: selected.flightId,
                origin_airport: selected.origin,
                destination_airport: selected.destination,
                departure: selected.departure,
                arrival: selected.arrival,
                stop_count: selected.stopCount,
                cabin: selected.cabin,
                fare_class: selected.fareClass,
                seat_type: "aisle",
                base_fare_minor: Number(selected.baseFare.amount) * 100,
                taxes_and_fees_minor: Number(selected.taxesAndFees.amount) * 100,
                total_additional_cost_minor: Number(selected.additionalCost.amount) * 100,
                currency: selected.additionalCost.currency,
              })}
              data-trust-target="northstar.confirm-rebooking"
              disabled={busy}
              onClick={() => void commit()}
              type="button"
            >
              {busy ? "Submitting once…" : "Confirm rebooking"}
            </button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
