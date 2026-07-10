import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  ActionProposalSchema,
  RunManifestSchema,
  TaskContractBodySchema,
  canonicalJson,
  createTaskContract,
  sha256Hex,
  uuidv7,
} from "../src/index";

const golden = JSON.parse(
  readFileSync(new URL("../fixtures/jcs-golden.json", import.meta.url), "utf8"),
) as {
  value: unknown;
  canonical: string;
  sha256: string;
  hmac_key_hex: string;
  hmac_sha256: string;
};

const body = TaskContractBodySchema.parse({
  schema_version: "1.0.0",
  contract_id: uuidv7(),
  goal: "Recover the cancelled trip.",
  hard_constraints: [{ field: "origin", operator: "equals", value: "SFO" }],
  preferences: [{ field: "stop_count", direction: "ascending" }],
  success_predicates: [{ predicate_id: "replacement_booking_confirmed", parameters: {} }],
  forbidden_effects: ["duplicate_booking"],
  approval_rules: [
    {
      effect: "FINANCIAL_OR_CONTRACTUAL_COMMIT",
      rule: "exact_context_single_use_grant",
    },
  ],
  allowed_origins: ["http://northstar.localhost:3001"],
  allowed_tools: ["ui.click"],
  scenario_now: "2030-06-13T09:00:00-07:00",
  max_steps: 60,
  max_model_calls: 45,
  max_replans: 4,
  max_wall_time_seconds: 600,
  max_model_cost_usd: "1.50",
  max_read_retries_per_step: 2,
  max_commit_retries: 0,
  non_progress_limit: 2,
  approval_ttl_seconds: 180,
  max_commit_observation_age_seconds: 15,
});

describe("actor-visible contracts", () => {
  it("matches the Python golden contract hash", () => {
    const fixture = JSON.parse(
      readFileSync(resolve(process.cwd(), "fixtures/task-contract.clean.v1.json"), "utf8"),
    ) as unknown;
    const contract = createTaskContract(TaskContractBodySchema.parse(fixture));
    expect(contract.content_hash).toBe(
      "0ba3ced2d02ea0d2ce6fdd1f5c1a042aa204df5b2a84f3e9a94281ca5722ceb1",
    );
  });
  it("matches the shared cross-language JCS, hash, and signature fixture", () => {
    const canonical = canonicalJson(golden.value);
    expect(canonical).toBe(golden.canonical);
    expect(sha256Hex(golden.value)).toBe(golden.sha256);
    expect(
      createHmac("sha256", Buffer.from(golden.hmac_key_hex, "hex")).update(canonical).digest("hex"),
    ).toBe(golden.hmac_sha256);
  });

  it("hashes the canonical actor contract without evaluation metadata", () => {
    const contract = createTaskContract(body);

    expect(contract.content_hash).toMatch(/^[a-f0-9]{64}$/);
    expect("scenario_seed" in contract).toBe(false);
    expect("fault_id" in contract).toBe(false);
  });

  it("rejects actor-supplied origin, effect, and evaluation metadata", () => {
    expect(() =>
      ActionProposalSchema.parse({
        schema_version: "1.0.0",
        action_id: uuidv7(),
        run_id: uuidv7(),
        step_number: 1,
        plan_version: 0,
        observation_id: uuidv7(),
        observation_hash: "a".repeat(64),
        tool: "ui.click",
        target_description: "Confirm exchange",
        coordinates_normalized: [500, 500],
        text: null,
        expected_postconditions: [],
        grounding_confidence: "0.9",
        decision_summary: "Confirm the approved offer.",
        target_origin: "http://northstar.localhost:3001",
        effect_class: "READ",
        fault_id: "F-AMBIGUOUS-COMMIT",
      }),
    ).toThrow();
  });

  it("keeps sealed metadata in a separate manifest", () => {
    const contract = createTaskContract(body);
    const manifest = RunManifestSchema.parse({
      schema_version: "1.0.0",
      run_id: uuidv7(),
      task_contract_hash: contract.content_hash,
      scenario_id: "disrupted_trip_v1",
      scenario_seed: 1301,
      fixture_version: "disrupted-trip-v1",
      fault_manifest_version: "1.0.0",
      fault_id: "F-AMBIGUOUS-COMMIT",
      fault_parameters: {},
      expected_terminal_outcome: "SUCCEEDED",
      oracle_version: "1.0.0",
      retention_class: "published_benchmark",
    });

    expect(manifest.scenario_seed).toBe(1301);
  });
});
