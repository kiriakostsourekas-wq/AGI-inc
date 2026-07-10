"use client";

import { useEffect, useState } from "react";

import { Badge } from "@trust/ui";

import { AppChrome, StatusPill } from "@/components/product-primitives";
import { replayFrames } from "@/lib/mock-data";
import {
  isFixtureRunId,
  RuntimeApiClient,
  type ReplayBundle,
  type ReplayFrameRecord,
} from "@/lib/runtime-api";
import { configuredRuntime, readRuntimeSession } from "@/lib/runtime-session";

function FixtureReplayPlayer({ runId }: { runId: string }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const frame = replayFrames[index];

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(
      () =>
        setIndex((current) => {
          if (current >= replayFrames.length - 1) {
            setPlaying(false);
            return current;
          }
          return current + 1;
        }),
      1800,
    );
    return () => window.clearInterval(timer);
  }, [playing]);

  return (
    <div className="replay-shell">
      <div className="replay-toolbar">
        <div className="replay-toolbar__identity">
          <Badge tone="accent">Replay preview</Badge>
          <strong>{runId}</strong>
          <span className="ui-mono">fixture trace · no model calls</span>
        </div>
        <div className="replay-toolbar__controls">
          <button
            className="replay-icon-button"
            disabled={index === 0}
            onClick={() => setIndex((value) => Math.max(0, value - 1))}
            aria-label="Previous event"
          >
            ←
          </button>
          <button
            className="replay-icon-button"
            onClick={() => setPlaying((value) => !value)}
            aria-label={playing ? "Pause replay" : "Play replay"}
          >
            {playing ? "Ⅱ" : "▶"}
          </button>
          <button
            className="replay-icon-button"
            disabled={index === replayFrames.length - 1}
            onClick={() => setIndex((value) => Math.min(replayFrames.length - 1, value + 1))}
            aria-label="Next event"
          >
            →
          </button>
        </div>
      </div>
      <div className="replay-main">
        <div className="replay-stage">
          <AppChrome
            app={`${frame.app.toLowerCase().replaceAll(" ", "-")}.localhost`}
            path={frame.path}
            tone={frame.tone}
          >
            <div className="replay-frame-content">
              <span className="replay-frame-content__app">{frame.app} · synthetic fixture</span>
              <span className="replay-frame-content__icon" aria-hidden="true">
                {index + 1}
              </span>
              <h2>{frame.title}</h2>
              <p>{frame.description}</p>
              <span className="replay-evidence">{frame.evidence}</span>
            </div>
          </AppChrome>
        </div>
        <aside className="replay-inspector">
          <div className="replay-inspector__chapter">
            <span>
              Frame {index + 1} / {replayFrames.length}
            </span>
            <StatusPill tone={frame.tone}>{frame.status}</StatusPill>
          </div>
          <h2>{frame.chapter}</h2>
          <p>{frame.description}</p>
          <div className="replay-frame-meta">
            <span>Observable evidence</span>
            <strong>{frame.evidence}</strong>
          </div>
          <div className="replay-frame-meta">
            <span>Honesty boundary</span>
            <strong>
              This interface is backed by committed synthetic fixture data, not a live or previously
              measured model execution.
            </strong>
          </div>
        </aside>
      </div>
      <div className="replay-scrubber">
        <div className="replay-scrubber__chapters">
          {replayFrames.map((item, itemIndex) => (
            <button
              className="replay-chapter"
              data-active={itemIndex === index}
              key={item.id}
              onClick={() => {
                setIndex(itemIndex);
                setPlaying(false);
              }}
              title={item.chapter}
            >
              {String(itemIndex + 1).padStart(2, "0")} · {item.chapter}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function safeReplayScreenshot(value: string | undefined): string | undefined {
  if (!value) return undefined;
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ||
      (url.protocol === "http:" && url.hostname.endsWith("localhost"))
      ? value
      : undefined;
  } catch {
    return undefined;
  }
}

function RecordedFrame({ frame, index }: { frame: ReplayFrameRecord; index: number }) {
  const screenshot = safeReplayScreenshot(frame.screenshot_url);
  if (screenshot)
    return (
      <img
        className="runtime-screenshot"
        src={screenshot}
        alt={`Recorded synthetic browser frame ${index + 1}: ${frame.title}`}
      />
    );
  return (
    <AppChrome
      app={`${frame.app.toLowerCase().replaceAll(" ", "-")}.localhost`}
      path={frame.path}
      tone={frame.tone}
    >
      <div className="replay-frame-content">
        <span className="replay-frame-content__app">{frame.app} · recorded synthetic run</span>
        <span className="replay-frame-content__icon" aria-hidden="true">
          {index + 1}
        </span>
        <h2>{frame.title}</h2>
        <p>{frame.description}</p>
        <span className="replay-evidence">{frame.evidence}</span>
      </div>
    </AppChrome>
  );
}

function RecordedReplayPlayer({ replay }: { replay: ReplayBundle }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const frame = replay.frames[index];

  useEffect(() => {
    if (!playing || !frame) return;
    const timer = window.setInterval(
      () =>
        setIndex((current) => {
          if (current >= replay.frames.length - 1) {
            setPlaying(false);
            return current;
          }
          return current + 1;
        }),
      1_800,
    );
    return () => window.clearInterval(timer);
  }, [playing, frame, replay.frames.length]);

  if (!frame)
    return (
      <div className="runtime-state-shell">
        <Badge tone="warning">Empty replay</Badge>
        <h2>No recorded frames</h2>
        <p>
          The runtime returned an honest empty replay bundle. No fixture frames were substituted.
        </p>
      </div>
    );
  return (
    <div className="replay-shell">
      <div className="replay-toolbar">
        <div className="replay-toolbar__identity">
          <Badge tone="accent">Recorded replay</Badge>
          <strong>{replay.run_id}</strong>
          <span className="ui-mono">
            {replay.source_execution_kind.replaceAll("_", " ")} · no model calls
          </span>
        </div>
        <div className="replay-toolbar__controls">
          <button
            className="replay-icon-button"
            disabled={index === 0}
            onClick={() => setIndex((value) => Math.max(0, value - 1))}
            aria-label="Previous event"
          >
            ←
          </button>
          <button
            className="replay-icon-button"
            onClick={() => setPlaying((value) => !value)}
            aria-label={playing ? "Pause replay" : "Play replay"}
          >
            {playing ? "Ⅱ" : "▶"}
          </button>
          <button
            className="replay-icon-button"
            disabled={index === replay.frames.length - 1}
            onClick={() => setIndex((value) => Math.min(replay.frames.length - 1, value + 1))}
            aria-label="Next event"
          >
            →
          </button>
        </div>
      </div>
      <div className="replay-main">
        <div className="replay-stage">
          <RecordedFrame frame={frame} index={index} />
        </div>
        <aside className="replay-inspector">
          <div className="replay-inspector__chapter">
            <span>
              Frame {index + 1} / {replay.frames.length}
            </span>
            <StatusPill tone={frame.tone}>{frame.status}</StatusPill>
          </div>
          <h2>{frame.chapter}</h2>
          <p>{frame.description}</p>
          <div className="replay-frame-meta">
            <span>Observable evidence</span>
            <strong>{frame.evidence}</strong>
          </div>
          <div className="replay-frame-meta">
            <span>Honesty boundary</span>
            <strong>
              Immutable recorded artifacts fetched from the replay endpoint. Playback does not call
              a model or claim to be live.
            </strong>
          </div>
        </aside>
      </div>
      <div className="replay-scrubber">
        <div className="replay-scrubber__chapters">
          {replay.frames.map((item, itemIndex) => (
            <button
              className="replay-chapter"
              data-active={itemIndex === index}
              key={item.id}
              onClick={() => {
                setIndex(itemIndex);
                setPlaying(false);
              }}
              title={item.chapter}
            >
              {String(item.sequence_no).padStart(2, "0")} · {item.chapter}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function LiveReplayLoader({ runId }: { runId: string }) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ready"; replay: ReplayBundle }
    | { kind: "error"; message: string }
  >({ kind: "loading" });
  useEffect(() => {
    const abort = new AbortController();
    const runtime = configuredRuntime();
    const session = readRuntimeSession();
    if (!runtime.enabled || !session) {
      setState({
        kind: "error",
        message: "An active live runtime session is required. No fixture replay was substituted.",
      });
      return () => abort.abort();
    }
    const client = new RuntimeApiClient({ baseUrl: runtime.baseUrl });
    void client
      .getRunReplay(runId, abort.signal)
      .then((replay) => {
        if (!abort.signal.aborted) setState({ kind: "ready", replay });
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted)
          setState({
            kind: "error",
            message:
              error instanceof Error ? error.message : "The recorded replay could not be loaded.",
          });
      });
    return () => abort.abort();
  }, [runId]);
  if (state.kind === "ready") return <RecordedReplayPlayer replay={state.replay} />;
  return (
    <div className="runtime-state-shell" aria-live="polite">
      <Badge tone={state.kind === "loading" ? "accent" : "danger"}>{state.kind}</Badge>
      <h2>{state.kind === "loading" ? "Loading recorded artifacts…" : "Replay unavailable"}</h2>
      <p>
        {state.kind === "loading"
          ? "Fetching immutable frames without a model call."
          : state.message}
      </p>
      <strong>No fixture data has been substituted.</strong>
    </div>
  );
}

export function ReplayPlayer({ runId }: { runId: string }) {
  return isFixtureRunId(runId) ? (
    <FixtureReplayPlayer runId={runId} />
  ) : (
    <LiveReplayLoader runId={runId} />
  );
}
