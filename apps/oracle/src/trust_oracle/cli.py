"""Operator-only evaluation commands."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx
import typer

from .config import get_settings
from .worker import OracleEvaluationStore, run_oracle_loop

app = typer.Typer(help="Operate the sealed Trust Runtime evaluator.", no_args_is_help=True)


@app.command()
def paired(
    runtime_base_url: str = typer.Option("http://localhost:8000", envvar="RUNTIME_BASE_URL"),
    maximum_total_cost_usd: str = typer.Option("100.00"),
) -> None:
    """Queue the predeclared 30-pair live evaluation through the operator API."""

    token = os.getenv("EVALUATION_OPERATOR_TOKEN")
    if not token:
        typer.echo("EVALUATION_OPERATOR_TOKEN is required; no evaluation was queued.", err=True)
        raise typer.Exit(code=2)
    try:
        response = httpx.post(
            f"{runtime_base_url.rstrip('/')}/v1/evaluations",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": f"eval-{uuid4()}",
            },
            json={
                "plan_id": "paired-primary-v1",
                "maximum_total_cost_usd": maximum_total_cost_usd,
            },
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        typer.echo(f"Evaluation was not queued: {error}", err=True)
        raise typer.Exit(code=2) from error
    payload = response.json()
    typer.echo(
        f"queued evaluation={payload['evaluation_id']} "
        f"intents={payload['intended_execution_count']} status={payload['status']}"
    )


@app.command()
def status(
    evaluation_id: str,
    runtime_base_url: str = typer.Option("http://localhost:8000", envvar="RUNTIME_BASE_URL"),
) -> None:
    """Read durable evaluation status without interpreting pending rows as results."""

    token = os.getenv("EVALUATION_OPERATOR_TOKEN")
    if not token:
        typer.echo("EVALUATION_OPERATOR_TOKEN is required.", err=True)
        raise typer.Exit(code=2)
    response = httpx.get(
        f"{runtime_base_url.rstrip('/')}/v1/evaluations/{evaluation_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if not response.is_success:
        typer.echo(f"Evaluation lookup failed with HTTP {response.status_code}.", err=True)
        raise typer.Exit(code=2)
    payload = response.json()
    typer.echo(
        f"evaluation={payload['evaluation_id']} status={payload['status']} "
        f"counts={payload['execution_status_counts']}"
    )


@app.command()
def report(evaluation_id: UUID, output: Path = Path("evals/reports/generated")) -> None:
    """Generate and persist metrics only from a complete sealed raw batch."""

    root = Path(__file__).resolve().parents[4]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from evals.reporting import write_report_bundle

    settings = get_settings()
    store = OracleEvaluationStore(settings)
    try:
        results = store.export_results(evaluation_id)
        manifest = store.load_manifest(evaluation_id)
        output.mkdir(parents=True, exist_ok=True)
        (output / "raw-results.json").write_text(
            json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        summary_value = write_report_bundle(
            plan=manifest,
            results=results,
            output_directory=output,
        )
        summary = cast(dict[str, object], summary_value)
        metric_count = store.persist_metric_summary(
            evaluation_id=evaluation_id,
            summary=summary,
        )
    except (RuntimeError, ValueError, OSError) as error:
        typer.echo(f"Evaluation report refused: {error}", err=True)
        raise typer.Exit(code=2) from error
    finally:
        store.close()
    typer.echo(f"generated evaluation={evaluation_id} metrics={metric_count}")


@app.command("export")
def export_results(evaluation_id: UUID, output: Path) -> None:
    """Export a complete immutable batch in the strict raw-attempt schema."""

    settings = get_settings()
    store = OracleEvaluationStore(settings)
    try:
        results = store.export_results(evaluation_id)
    except RuntimeError as error:
        typer.echo(f"Evaluation export refused: {error}", err=True)
        raise typer.Exit(code=2) from error
    finally:
        store.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    attempts = results.get("attempts")
    if not isinstance(attempts, list):
        raise RuntimeError("exported result is missing attempts")
    typer.echo(f"exported evaluation={evaluation_id} attempts={len(cast(list[object], attempts))}")


@app.command("worker")
def worker_command(
    max_jobs: int = typer.Option(1, min=0, help="Zero runs continuously."),
    poll_seconds: float = typer.Option(1.0, min=0.1, max=60),
) -> None:
    """Score queued terminal runs from sealed sandbox state."""

    completed = asyncio.run(
        run_oracle_loop(
            settings=get_settings(),
            max_jobs=max_jobs,
            poll_seconds=poll_seconds,
        )
    )
    typer.echo(f"oracle evaluation jobs completed={completed}")
