"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Badge, Button, Panel, buttonClassName } from "@trust/ui";

import { SyntheticNotice } from "@/components/product-primitives";
import { buildTaskRequest, faultOptions } from "@/lib/mock-data";
import { buildCreateRunRequest } from "@/lib/run-input";
import { RuntimeApiClient, RuntimeApiError } from "@/lib/runtime-api";
import { configuredRuntime, readRuntimeSession, writeRuntimeSession } from "@/lib/runtime-session";
import type { FaultId } from "@/lib/types";

function FaultCards({
  options,
  selected,
  onChange,
}: {
  options: typeof faultOptions;
  selected: FaultId;
  onChange: (id: FaultId) => void;
}) {
  return (
    <div className="fault-grid">
      {options.map((fault) => (
        <label className="fault-option" data-selected={selected === fault.id} key={fault.id}>
          <input
            type="radio"
            name="fault"
            value={fault.id}
            checked={selected === fault.id}
            onChange={() => onChange(fault.id)}
          />
          <span className="fault-option__top">
            <Badge tone="neutral">{fault.shortLabel}</Badge>
            <span className="fault-option__radio" aria-hidden="true" />
          </span>
          <strong>{fault.title}</strong>
          <p>{fault.description}</p>
        </label>
      ))}
    </div>
  );
}

export function ScenarioComposer() {
  const router = useRouter();
  const [maxCost, setMaxCost] = useState(450);
  const [departureAfter, setDepartureAfter] = useState("12:00 PM PT");
  const [arrivalBy, setArrivalBy] = useState("8:00 PM PT");
  const [aisleRequired, setAisleRequired] = useState(true);
  const [fault, setFault] = useState<FaultId>("F-AMBIGUOUS-COMMIT");
  const [seed, setSeed] = useState("1301");
  const [launchState, setLaunchState] = useState<"idle" | "creating" | "rate_limited" | "error">(
    "idle",
  );
  const [launchMessage, setLaunchMessage] = useState<string>();

  const request = useMemo(
    () => buildTaskRequest({ maxCost, departureAfter, arrivalBy, aisleRequired }),
    [maxCost, departureAfter, arrivalBy, aisleRequired],
  );
  const selectedFault = faultOptions.find((option) => option.id === fault) ?? faultOptions[0];
  const runtime = configuredRuntime();
  const [seedMinimum, seedMaximum] = selectedFault.seedRange.split("–").map(Number);

  async function launchLiveRun() {
    setLaunchState("creating");
    setLaunchMessage(undefined);
    try {
      const stored = readRuntimeSession();
      const client = new RuntimeApiClient({ baseUrl: runtime.baseUrl });
      if (!stored) writeRuntimeSession(await client.createSession());
      const created = await client.createRun(
        buildCreateRunRequest({
          maxCost,
          departureAfter,
          arrivalBy,
          aisleRequired,
          request,
          fault,
          seed: Number(seed),
        }),
      );
      router.push(`/runs/${created.run_id}`);
    } catch (error: unknown) {
      if (error instanceof RuntimeApiError && error.status === 429) {
        setLaunchState("rate_limited");
        setLaunchMessage(
          `${error.message}${error.retryAfterSeconds ? ` Retry after ${error.retryAfterSeconds} seconds.` : ""}`,
        );
      } else {
        setLaunchState("error");
        setLaunchMessage(
          error instanceof Error ? error.message : "The live runtime could not create this run.",
        );
      }
    }
  }

  return (
    <div className="demo-grid">
      <Panel className="composer-panel">
        <div className="panel-title-row">
          <div>
            <h2>Compose the rescue contract</h2>
            <p>Structured controls compile to an immutable task once the run starts.</p>
          </div>
          <Badge tone="accent">Step 1 of 2</Badge>
        </div>
        <div className="scenario-summary">
          <span className="scenario-summary__avatar" aria-hidden="true">
            MC
          </span>
          <div>
            <strong>Maya Chen · NST-P7Q4M2</strong>
            <span>NS217 cancelled · SFO → SEA · Jun 14, 2030</span>
          </div>
          <Badge tone="danger">Cancelled</Badge>
        </div>
        <div className="field-grid">
          <label className="field">
            <span className="field__label">
              Route <small>Fixed benchmark scope</small>
            </span>
            <input readOnly value="SFO → SEA" />
          </label>
          <label className="field">
            <span className="field__label">
              Cabin <small>Fixed</small>
            </span>
            <input readOnly value="Economy" />
          </label>
          <label className="field">
            <span className="field__label">Leave after</span>
            <select
              value={departureAfter}
              onChange={(event) => setDepartureAfter(event.target.value)}
            >
              <option>12:00 PM PT</option>
              <option>1:00 PM PT</option>
              <option>2:00 PM PT</option>
            </select>
          </label>
          <label className="field">
            <span className="field__label">Arrive by</span>
            <select value={arrivalBy} onChange={(event) => setArrivalBy(event.target.value)}>
              <option>8:00 PM PT</option>
              <option>7:30 PM PT</option>
              <option>7:00 PM PT</option>
            </select>
          </label>
          <label className="field">
            <span className="field__label">
              Maximum additional cost <small>USD</small>
            </span>
            <input
              type="number"
              min={100}
              max={600}
              step={25}
              value={maxCost}
              onChange={(event) => setMaxCost(Number(event.target.value))}
            />
          </label>
          <label className="field">
            <span className="field__label">Seat constraint</span>
            <span className="toggle-field">
              <span>Require aisle seat</span>
              <input
                type="checkbox"
                checked={aisleRequired}
                onChange={(event) => setAisleRequired(event.target.checked)}
              />
            </span>
          </label>
        </div>
        <div className="request-preview">
          <span>Generated user request</span>
          <p>{request}</p>
        </div>
        <div className="composer-divider" />
        <div className="panel-title-row">
          <div>
            <h3>Choose a published failure</h3>
            <p>The actor cannot see fault IDs, seeds, or expected answers.</p>
          </div>
          <Badge tone="neutral">Reproducible</Badge>
        </div>
        <FaultCards
          options={faultOptions.filter((option) => !option.advanced)}
          selected={fault}
          onChange={(id) => {
            setFault(id);
            const range = faultOptions.find((item) => item.id === id)?.seedRange;
            setSeed(range?.split("–")[0] ?? "1001");
          }}
        />
        <details className="advanced-faults">
          <summary>Advanced safety gates</summary>
          <div className="advanced-faults__content">
            <FaultCards
              options={faultOptions.filter((option) => option.advanced)}
              selected={fault}
              onChange={(id) => {
                setFault(id);
                setSeed(id === "S-PROMPT-INJECTION" ? "2101" : "2201");
              }}
            />
          </div>
        </details>
      </Panel>

      <Panel className="contract-preview" elevated>
        <div className="panel-title-row">
          <div>
            <h2>Preflight review</h2>
            <p>
              {runtime.enabled
                ? "The runtime compiles and hashes the reviewed actor contract on start."
                : "This deployment opens a deterministic fixture run."}
            </p>
          </div>
          <Badge tone={runtime.enabled ? "accent" : "neutral"}>
            {runtime.enabled ? "Live runtime configured" : "Fixture mode"}
          </Badge>
        </div>
        <SyntheticNotice compact />
        <div className="contract-preview__hash">
          <span>Contract hash</span>
          <code>generated-at-start</code>
        </div>
        <div className="contract-preview__section">
          <h3>Hard constraints</h3>
          {[
            "SFO → SEA · economy",
            `Depart ≥ ${departureAfter}`,
            `Arrive ≤ ${arrivalBy}`,
            aisleRequired ? "Aisle seat required" : "Seat type flexible",
            `Additional cost ≤ $${maxCost}.00 USD`,
          ].map((rule) => (
            <div className="contract-rule" key={rule}>
              {rule}
            </div>
          ))}
        </div>
        <div className="contract-preview__section">
          <h3>Authority</h3>
          <div className="contract-rule">Booking requires exact single-use approval</div>
          <div className="contract-rule">
            Calendar mutation allowed only after booking verification
          </div>
          <div className="contract-rule">No commit retry while outcome is unknown</div>
        </div>
        <div className="contract-preview__section">
          <h3>Failure fixture</h3>
          <div className="evidence-row">
            <div>
              <strong>{selectedFault.title}</strong>
              <span>{selectedFault.expectedBehavior}</span>
            </div>
            <Badge tone="warning">Seed {seed}</Badge>
          </div>
          <label className="field seed-field">
            <span className="field__label">
              Published seed <small>{selectedFault.seedRange}</small>
            </span>
            <input
              type="number"
              min={seedMinimum}
              max={seedMaximum || seedMinimum}
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
            />
          </label>
        </div>
        <div className="run-launch-box">
          {runtime.enabled ? (
            <Button
              size="lg"
              disabled={launchState === "creating"}
              onClick={() => void launchLiveRun()}
            >
              {launchState === "creating"
                ? "Creating server contract…"
                : "Start protected runtime run"}{" "}
              <span aria-hidden="true">→</span>
            </Button>
          ) : null}
          <Link
            className={buttonClassName({
              variant: runtime.enabled ? "ghost" : "primary",
              size: "lg",
            })}
            href={`/runs/mock-${seed}?fault=${fault}`}
          >
            Open labeled fixture run
          </Link>
          {launchMessage ? (
            <p className="launch-error" role="alert">
              {launchMessage}
            </p>
          ) : null}
          <p>
            {runtime.enabled
              ? "If live capacity is unavailable, the separate fixture route remains explicitly labeled and never substitutes for this run."
              : "Honesty boundary: this route previews the product UI with committed fixture data. It is not a live model execution."}
          </p>
        </div>
      </Panel>
    </div>
  );
}
