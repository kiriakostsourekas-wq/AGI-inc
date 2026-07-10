from pathlib import Path

import pytest
import typer

from trust_runtime.cli import _clear_local_artifacts
from trust_runtime.config import AppEnvironment, ObjectStorageBackend, RuntimeSettings


def test_clear_local_artifacts_removes_only_dedicated_artifact_contents(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    nested = artifact_root / "run-1"
    nested.mkdir(parents=True)
    (nested / "frame.png").write_bytes(b"png")
    (nested / "frame.json").write_text("{}")
    settings = RuntimeSettings(artifact_storage_dir=artifact_root)

    assert _clear_local_artifacts(settings) == 2
    assert artifact_root.is_dir()
    assert list(artifact_root.iterdir()) == []


def test_clear_local_artifacts_refuses_production_s3_and_unsafe_roots(tmp_path: Path) -> None:
    base = RuntimeSettings(artifact_storage_dir=tmp_path / "artifacts")
    with pytest.raises(typer.BadParameter, match="production"):
        _clear_local_artifacts(base.model_copy(update={"app_env": AppEnvironment.PRODUCTION}))
    with pytest.raises(typer.BadParameter, match="S3"):
        _clear_local_artifacts(
            base.model_copy(update={"object_storage_backend": ObjectStorageBackend.S3})
        )
    with pytest.raises(typer.BadParameter, match="dedicated"):
        _clear_local_artifacts(base.model_copy(update={"artifact_storage_dir": Path.cwd()}))
