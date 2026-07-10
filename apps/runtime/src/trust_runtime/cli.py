"""Operational CLI commands used by the root Makefile."""

import json
import shutil
from pathlib import Path

import typer

from .api import create_app
from .config import AppEnvironment, ObjectStorageBackend, RuntimeSettings

app = typer.Typer(no_args_is_help=True, help="Trust runtime operations")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the API development server."""

    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)


@app.command("show-config")
def show_config() -> None:
    """Print a redacted configuration summary."""

    typer.echo(json.dumps(RuntimeSettings().safe_summary(), indent=2, default=str))


@app.command("reset-demo")
def reset_demo() -> None:
    """Reset local replay artifacts before launching the in-memory demo."""

    settings = RuntimeSettings()
    removed = _clear_local_artifacts(settings)
    typer.echo(
        f"Removed {removed} local artifact files. "
        "The browser worker will reset sandbox state through its authenticated reset endpoint."
    )


@app.command("clean-data")
def clean_data() -> None:
    """Delete local filesystem artifacts; the Make target also removes the local DB volume."""

    settings = RuntimeSettings()
    removed = _clear_local_artifacts(settings)
    typer.echo(f"Removed {removed} local artifact files.")


def _clear_local_artifacts(settings: RuntimeSettings) -> int:
    if settings.app_env is AppEnvironment.PRODUCTION:
        raise typer.BadParameter("local data cleanup is disabled in production")
    if settings.object_storage_backend is not ObjectStorageBackend.FILESYSTEM:
        raise typer.BadParameter("local data cleanup cannot delete an S3-compatible bucket")

    root = settings.artifact_storage_dir.resolve()
    unsafe_roots = {Path("/").resolve(), Path.cwd().resolve(), Path.home().resolve()}
    if root in unsafe_roots:
        raise typer.BadParameter("ARTIFACT_STORAGE_DIR must be a dedicated subdirectory")
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return 0

    removed = sum(1 for path in root.rglob("*") if path.is_file())
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return removed
