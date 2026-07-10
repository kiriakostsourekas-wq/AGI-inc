import { createHash } from "node:crypto";

import canonicalize from "canonicalize";
import { v7 as uuidv7 } from "uuid";
import { z } from "zod";

const Sha256Schema = z.string().regex(/^[a-f0-9]{64}$/);
const UuidSchema = z.uuid();
const OriginSchema = z.url().superRefine((value, context) => {
  const url = new URL(value);
  const localhost = url.hostname === "localhost" || url.hostname.endsWith(".localhost");
  if (url.protocol !== "https:" && !(url.protocol === "http:" && localhost)) {
    context.addIssue({
      code: "custom",
      message: "origin must be HTTPS outside localhost",
    });
  }
  if (
    url.username ||
    url.password ||
    (url.pathname !== "" && url.pathname !== "/") ||
    url.search ||
    url.hash
  ) {
    context.addIssue({
      code: "custom",
      message: "origin must not include credentials, path, query, or fragment",
    });
  }
});

export const MoneySchema = z
  .object({
    amount_minor: z.int().nonnegative().max(Number.MAX_SAFE_INTEGER),
    currency: z.string().regex(/^[A-Z]{3}$/),
  })
  .strict();

export const ConstraintSchema = z
  .object({
    field: z.string().min(1),
    operator: z.enum(["equals", "on_or_after", "on_or_before", "less_than_or_equal"]),
    value: z.json(),
  })
  .strict();

export const PreferenceSchema = z
  .object({
    field: z.string().min(1),
    direction: z.enum(["ascending", "descending"]),
  })
  .strict();

export const SuccessPredicateSchema = z
  .object({
    predicate_id: z.string().min(1),
    parameters: z.record(z.string(), z.json()).default({}),
  })
  .strict();

export const ApprovalRuleSchema = z
  .object({
    effect: z.literal("FINANCIAL_OR_CONTRACTUAL_COMMIT"),
    rule: z.string().min(1),
  })
  .strict();

export const ActorToolSchema = z.enum([
  "ui.open_url",
  "ui.click",
  "ui.double_click",
  "ui.type_text",
  "ui.keypress",
  "ui.scroll",
  "ui.back",
  "ui.wait",
  "runtime.finish",
  "runtime.safe_abort",
]);

export const TaskContractBodySchema = z
  .object({
    schema_version: z.literal("1.0.0"),
    contract_id: UuidSchema,
    goal: z.string().min(1).max(2_000),
    hard_constraints: z.array(ConstraintSchema).min(1),
    preferences: z.array(PreferenceSchema),
    success_predicates: z.array(SuccessPredicateSchema).min(1),
    forbidden_effects: z.array(z.string().min(1)),
    approval_rules: z.array(ApprovalRuleSchema),
    allowed_origins: z.array(OriginSchema).min(1),
    allowed_tools: z.array(ActorToolSchema).min(1),
    scenario_now: z.iso.datetime({ offset: true }),
    max_steps: z.int().positive().max(500),
    max_model_calls: z.int().positive().max(500),
    max_replans: z.int().nonnegative().max(50),
    max_wall_time_seconds: z.int().positive().max(3_600),
    max_model_cost_usd: z.string().regex(/^\d+\.\d{2}$/),
    max_read_retries_per_step: z.int().nonnegative().max(10),
    max_commit_retries: z.literal(0),
    non_progress_limit: z.int().positive().max(10),
    approval_ttl_seconds: z.int().min(15).max(900),
    max_commit_observation_age_seconds: z.int().positive().max(120),
  })
  .strict();

export const TaskContractSchema = TaskContractBodySchema.extend({
  content_hash: Sha256Schema,
}).strict();

export const RunStatusSchema = z.enum([
  "CREATED",
  "ENV_RESET",
  "CONTRACT_VALIDATED",
  "OBSERVING",
  "PLANNING",
  "REPLANNING",
  "ACTION_PROPOSED",
  "POLICY_CHECKING",
  "WAITING_APPROVAL",
  "EXECUTING",
  "VERIFYING",
  "RECOVERING",
  "OUTCOME_UNKNOWN",
  "FINALIZING",
  "SUCCEEDED",
  "PARTIAL_SUCCESS",
  "HANDOFF_REQUIRED",
  "FAILED_OUTCOME_UNKNOWN",
  "SAFE_ABORTED",
  "FAILED",
  "CANCELLED",
]);

export const RunManifestSchema = z
  .object({
    schema_version: z.literal("1.0.0"),
    run_id: UuidSchema,
    task_contract_hash: Sha256Schema,
    scenario_id: z.string().min(1),
    scenario_seed: z.int().nonnegative(),
    fixture_version: z.string().min(1),
    fault_manifest_version: z.string().min(1),
    fault_id: z.string().min(1).nullable(),
    fault_parameters: z.record(z.string(), z.json()),
    expected_terminal_outcome: RunStatusSchema,
    oracle_version: z.string().min(1),
    retention_class: z.enum(["public_ephemeral", "published_benchmark"]),
  })
  .strict();

export const ActionProposalSchema = z
  .object({
    schema_version: z.literal("1.0.0"),
    action_id: UuidSchema,
    run_id: UuidSchema,
    step_number: z.int().nonnegative(),
    plan_version: z.int().nonnegative(),
    observation_id: UuidSchema,
    observation_hash: Sha256Schema,
    tool: ActorToolSchema,
    target_description: z.string().min(1).max(300),
    coordinates_normalized: z
      .tuple([z.int().min(0).max(1_000), z.int().min(0).max(1_000)])
      .nullable(),
    text: z.string().max(4_000).nullable(),
    expected_postconditions: z.array(
      z
        .object({
          predicate_id: z.string().min(1),
          parameters: z.record(z.string(), z.json()).default({}),
        })
        .strict(),
    ),
    grounding_confidence: z.string().regex(/^(0(\.\d+)?|1(\.0+)?)$/),
    decision_summary: z.string().min(1).max(500),
  })
  .strict();

export const EffectClassSchema = z.enum([
  "READ",
  "DRAFT",
  "REVERSIBLE_MUTATION",
  "EXTERNAL_COMMUNICATION",
  "FINANCIAL_OR_CONTRACTUAL_COMMIT",
  "CREDENTIAL_OR_IDENTITY",
]);

export type Money = z.infer<typeof MoneySchema>;
export type TaskContractBody = z.infer<typeof TaskContractBodySchema>;
export type TaskContract = z.infer<typeof TaskContractSchema>;
export type RunManifest = z.infer<typeof RunManifestSchema>;
export type RunStatus = z.infer<typeof RunStatusSchema>;
export type ActionProposal = z.infer<typeof ActionProposalSchema>;
export type EffectClass = z.infer<typeof EffectClassSchema>;

export function canonicalJson(value: unknown): string {
  const output = canonicalize(value);
  if (output === undefined) {
    throw new TypeError("value cannot be represented as canonical JSON");
  }
  return output;
}

export function sha256Hex(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

export function createTaskContract(body: TaskContractBody): TaskContract {
  const parsed = TaskContractBodySchema.parse(body);
  return TaskContractSchema.parse({
    ...parsed,
    content_hash: sha256Hex(parsed),
  });
}

export { uuidv7 };
