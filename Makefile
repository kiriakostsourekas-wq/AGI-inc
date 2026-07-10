COREPACK_HOME ?= /tmp/trust-runtime-corepack
PNPM := COREPACK_HOME=$(COREPACK_HOME) corepack pnpm@10.34.4
UV_CACHE_DIR ?= /tmp/trust-runtime-uv-cache
UV := UV_CACHE_DIR=$(UV_CACHE_DIR) uv

.PHONY: bootstrap dev lint typecheck test test-e2e eval-smoke eval-paired eval-worker-runtime eval-worker-oracle eval-report demo report clean clean-data db-up db-down db-migrate

bootstrap:
	$(PNPM) install
	$(UV) sync --all-packages
	$(UV) run --project apps/runtime playwright install chromium

dev:
	$(PNPM) dev

lint:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(PNPM) lint
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(PNPM) format:check

typecheck:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(PNPM) typecheck

test:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(PNPM) test
	$(UV) run --all-packages pytest tests/security

test-e2e:
	$(PNPM) test:e2e

eval-smoke:
	$(UV) run --project apps/runtime trust-eval smoke

eval-paired:
	$(UV) run --project apps/oracle trust-oracle paired

eval-worker-runtime:
	$(UV) run --project apps/runtime trust-eval worker --max-jobs 0

eval-worker-oracle:
	$(UV) run --project apps/oracle trust-oracle worker --max-jobs 0

eval-report:
	@test -n "$(EVALUATION_ID)" || (echo "EVALUATION_ID is required"; exit 2)
	$(UV) run --project apps/oracle trust-oracle report $(EVALUATION_ID) --output $${EVAL_OUTPUT:-evals/reports/generated}

demo:
	$(UV) run --project apps/runtime trust-runtime reset-demo
	$(PNPM) dev

report:
	@if [ ! -f "$${EVAL_RESULTS:-evals/results/latest.json}" ]; then \
		echo "Evaluation pending: no raw run artifact is present; no metrics were generated."; \
	else \
		$(UV) run --project apps/runtime python scripts/evaluation_report.py \
			--plan $${EVAL_PLAN:-evals/manifests/paired-primary.v1.json} \
			--results $${EVAL_RESULTS:-evals/results/latest.json} \
			--output $${EVAL_OUTPUT:-evals/reports/generated}; \
	fi

db-up:
	./scripts/compose.sh up -d postgres

db-down:
	./scripts/compose.sh down

db-migrate:
	$(UV) run --project apps/runtime alembic upgrade head

clean:
	$(PNPM) clean

clean-data:
	$(UV) run --project apps/runtime trust-runtime clean-data
	./scripts/compose.sh down --volumes
