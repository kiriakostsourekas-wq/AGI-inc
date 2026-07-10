"use client";

import { useState } from "react";
import { ErrorState, LoadingState } from "@/components/state-boundary";
import { readApiError, useSandboxState } from "@/components/use-sandbox-state";
import { formatDateTime, formatTime } from "@/lib/format";

const hours = ["11 AM", "12 PM", "1 PM", "2 PM", "3 PM", "4 PM", "5 PM", "6 PM", "7 PM", "8 PM"];

export function DayPlanApp({ runId }: { runId: string }) {
  const { state, error, loading, refresh } = useSandboxState(runId);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  if (loading && !state) return <LoadingState label="Opening synthetic calendar…" />;
  if (error && !state) return <ErrorState>{error}</ErrorState>;
  if (!state) return null;

  async function synchronizeCalendar() {
    if (!state?.booking) return;
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch("/api/sandbox/calendar", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "idempotency-key": `calendar:${runId}:${state.booking.bookingId}`,
        },
        body: JSON.stringify({
          runId,
          bookingId: state.booking.bookingId,
          evidence: ["manage_trip", "confirmation_email"],
        }),
      });
      if (!response.ok) throw new Error(await readApiError(response));
      setNotice("Travel block synchronized exactly once from verified booking evidence.");
      await refresh();
    } catch (updateError) {
      setNotice(
        updateError instanceof Error ? updateError.message : "Calendar update was rejected.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="calendar-page">
      <aside className="calendar-sidebar">
        <button className="today-button" type="button">
          Today
        </button>
        <div className="mini-calendar">
          <strong>June 2030</strong>
          <div className="mini-week">
            <span>M</span>
            <span>T</span>
            <span>W</span>
            <span>T</span>
            <span>F</span>
            <span>S</span>
            <span>S</span>
          </div>
          <div className="mini-days">
            {[10, 11, 12, 13, 14, 15, 16].map((day) => (
              <span data-selected={day === 14} data-today={day === 13} key={day}>
                {day}
              </span>
            ))}
          </div>
        </div>
        <div className="calendar-list-label">My calendars</div>
        <label className="calendar-toggle">
          <input checked readOnly type="checkbox" />
          <span className="calendar-dot travel-dot" />
          Travel
        </label>
        <label className="calendar-toggle">
          <input checked readOnly type="checkbox" />
          <span className="calendar-dot personal-dot" />
          Personal
        </label>
      </aside>

      <section className="calendar-content">
        <header className="calendar-heading">
          <div>
            <span className="eyebrow">Saturday</span>
            <h1>June 14, 2030</h1>
          </div>
          <div className="calendar-controls" aria-label="Calendar view">
            <button type="button">‹</button>
            <button type="button">›</button>
            <button className="active" type="button">
              Day
            </button>
          </div>
        </header>

        <div className="calendar-guard">
          <div>
            <span className="guard-icon" aria-hidden="true">
              ◇
            </span>
            <div>
              <strong>Booking-verification guard</strong>
              <span>
                DayPlan accepts one update only after Manage Trip and a matching confirmation email
                are observable.
              </span>
            </div>
          </div>
          <button
            className="primary-button"
            data-trust-context={
              state.booking
                ? JSON.stringify({
                    calendar_event_id: state.calendar.id,
                    verified_booking_id: state.booking.bookingId,
                    starts_at: state.booking.flight.departure,
                    ends_at: state.booking.flight.arrival,
                  })
                : undefined
            }
            data-trust-target="dayplan.save-calendar"
            disabled={!state.booking || state.calendar.updateCount > 0 || busy}
            onClick={() => void synchronizeCalendar()}
            type="button"
          >
            {state.calendar.updateCount > 0
              ? "Travel block synchronized"
              : busy
                ? "Verifying evidence…"
                : state.booking
                  ? "Verify and update travel block"
                  : "Waiting for verified booking"}
          </button>
        </div>

        {notice ? (
          <div className="notice notice-info" role="status">
            <strong>DayPlan</strong>
            <span>{notice}</span>
          </div>
        ) : null}

        <div className="day-grid">
          <div className="time-column">
            {hours.map((hour) => (
              <span key={hour}>{hour}</span>
            ))}
          </div>
          <div className="day-column">
            {hours.map((hour) => (
              <div className="hour-line" key={hour} />
            ))}
            <article
              className={`calendar-event event-${state.calendar.status}`}
              data-observable-state={`calendar-${state.calendar.status}`}
              style={{
                top: state.calendar.status === "confirmed" ? "31%" : "20%",
                height: state.calendar.status === "confirmed" ? "22%" : "20%",
              }}
            >
              <div className="event-status">
                {state.calendar.status === "confirmed" ? "Confirmed" : "Cancelled"}
              </div>
              <h2>{state.calendar.title}</h2>
              <p>
                {formatTime(state.calendar.start)}–{formatTime(state.calendar.end)} · SFO → SEA
              </p>
              <div className="event-evidence">
                <span>Reservation {state.calendar.reservationId}</span>
                {state.calendar.bookingId ? <span>Booking {state.calendar.bookingId}</span> : null}
              </div>
            </article>
          </div>
        </div>

        <footer className="calendar-footer">
          <span>Virtual schedule · {formatDateTime(state.virtualNow)}</span>
          <span>Mutation count · {state.calendar.updateCount}</span>
        </footer>
      </section>
    </div>
  );
}
