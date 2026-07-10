"""Immutable filesystem artifact backend with expiring signed retrieval URLs.

The filesystem implementation is the local S3-compatible boundary substitute. It
keeps bytes outside structured run state and stores only a small metadata sidecar.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlencode
from uuid import UUID

from trust_contracts import SecurityClock, uuid7


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: UUID
    run_id: UUID
    kind: str
    content_type: str
    byte_size: int
    sha256: str
    redaction_status: str
    source_url: str
    created_at: datetime
    expires_at: datetime
    sequence_no: int


class S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> Any: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete_objects(self, **kwargs: Any) -> Any: ...


def _serialize_record(record: ArtifactRecord) -> bytes:
    serialized = asdict(record)
    serialized["artifact_id"] = str(record.artifact_id)
    serialized["run_id"] = str(record.run_id)
    serialized["created_at"] = record.created_at.isoformat()
    serialized["expires_at"] = record.expires_at.isoformat()
    return json.dumps(serialized, separators=(",", ":"), sort_keys=True).encode()


def _deserialize_record(content: bytes | str) -> ArtifactRecord:
    raw = json.loads(content)
    return ArtifactRecord(
        artifact_id=UUID(raw["artifact_id"]),
        run_id=UUID(raw["run_id"]),
        kind=raw["kind"],
        content_type=raw["content_type"],
        byte_size=raw["byte_size"],
        sha256=raw["sha256"],
        redaction_status=raw["redaction_status"],
        source_url=raw["source_url"],
        created_at=datetime.fromisoformat(raw["created_at"]),
        expires_at=datetime.fromisoformat(raw["expires_at"]),
        sequence_no=raw["sequence_no"],
    )


class LocalArtifactStore:
    def __init__(self, *, root: Path, signing_key: bytes, clock: SecurityClock) -> None:
        if len(signing_key) < 32:
            raise ValueError("artifact signing key must contain at least 32 bytes")
        self._root = root
        self._signing_key = signing_key
        self._clock = clock
        self._root.mkdir(parents=True, exist_ok=True)

    def put_screenshot(
        self, *, run_id: UUID, content: bytes, source_url: str, sequence_no: int
    ) -> ArtifactRecord:
        artifact_id = uuid7()
        now = self._clock.now()
        record = ArtifactRecord(
            artifact_id=artifact_id,
            run_id=run_id,
            kind="browser_screenshot",
            content_type="image/png",
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            redaction_status="synthetic_fixture_verified",
            source_url=source_url,
            created_at=now,
            expires_at=now + timedelta(hours=24),
            sequence_no=sequence_no,
        )
        run_dir = self._root / str(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        object_path = run_dir / f"{artifact_id}.png"
        metadata_path = run_dir / f"{artifact_id}.json"
        with object_path.open("xb") as output:
            output.write(content)
        with metadata_path.open("xb") as output:
            output.write(_serialize_record(record))
        return record

    def list_for_run(self, run_id: UUID) -> list[ArtifactRecord]:
        run_dir = self._root / str(run_id)
        if not run_dir.is_dir():
            return []
        records = [self._read_metadata(path) for path in run_dir.glob("*.json")]
        return sorted(records, key=lambda item: (item.sequence_no, item.created_at))

    def signed_path(self, record: ArtifactRecord, *, ttl_seconds: int = 900) -> str:
        expires = int((self._clock.now() + timedelta(seconds=ttl_seconds)).timestamp())
        signature = self._signature(record.run_id, record.artifact_id, expires)
        query = urlencode({"expires": expires, "signature": signature})
        return f"/v1/runs/{record.run_id}/artifacts/{record.artifact_id}?{query}"

    def read_signed(
        self, *, run_id: UUID, artifact_id: UUID, expires: int, signature: str
    ) -> tuple[ArtifactRecord, bytes]:
        expected = self._signature(run_id, artifact_id, expires)
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("artifact signature is invalid")
        if int(self._clock.now().timestamp()) >= expires:
            raise PermissionError("artifact URL has expired")
        record = self._read_metadata(self._root / str(run_id) / f"{artifact_id}.json")
        if record.run_id != run_id or record.artifact_id != artifact_id:
            raise PermissionError("artifact scope does not match signed URL")
        content = (self._root / str(run_id) / f"{artifact_id}.png").read_bytes()
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise RuntimeError("artifact content hash does not match metadata")
        return record, content

    def cleanup_expired(self) -> int:
        deleted = 0
        for metadata_path in self._root.glob("*/*.json"):
            record = self._read_metadata(metadata_path)
            if self._clock.now() < record.expires_at:
                continue
            metadata_path.with_suffix(".png").unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            deleted += 1
        return deleted

    def _signature(self, run_id: UUID, artifact_id: UUID, expires: int) -> str:
        payload = f"{run_id}:{artifact_id}:{expires}".encode()
        return hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _read_metadata(path: Path) -> ArtifactRecord:
        return _deserialize_record(path.read_bytes())


class S3ArtifactStore:
    """S3-compatible immutable object backend with runtime-scoped signed retrieval."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        prefix: str,
        signing_key: bytes,
        clock: SecurityClock,
        client: S3Client | None = None,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("artifact signing key must contain at least 32 bytes")
        if not bucket or not endpoint_url or not access_key or not secret_key:
            raise ValueError("S3 artifact storage requires bucket, endpoint, and credentials")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._signing_key = signing_key
        self._clock = clock
        if client is None:
            boto3_module = importlib.import_module("boto3")
            client_factory = cast(Callable[..., object], boto3_module.client)
            client = cast(
                S3Client,
                client_factory(
                    "s3",
                    endpoint_url=endpoint_url,
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key,
                ),
            )
        self._client = client

    def put_screenshot(
        self, *, run_id: UUID, content: bytes, source_url: str, sequence_no: int
    ) -> ArtifactRecord:
        artifact_id = uuid7()
        now = self._clock.now()
        record = ArtifactRecord(
            artifact_id=artifact_id,
            run_id=run_id,
            kind="browser_screenshot",
            content_type="image/png",
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            redaction_status="synthetic_fixture_verified",
            source_url=source_url,
            created_at=now,
            expires_at=now + timedelta(hours=24),
            sequence_no=sequence_no,
        )
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._object_key(run_id, artifact_id, "png"),
            Body=content,
            ContentType="image/png",
            Metadata={"sha256": record.sha256, "run-id": str(run_id)},
        )
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._object_key(run_id, artifact_id, "json"),
            Body=_serialize_record(record),
            ContentType="application/json",
        )
        return record

    def list_for_run(self, run_id: UUID) -> list[ArtifactRecord]:
        records = [
            self._read_metadata(key)
            for key in self._list_keys(f"{self._base_prefix()}{run_id}/")
            if key.endswith(".json")
        ]
        return sorted(records, key=lambda item: (item.sequence_no, item.created_at))

    def signed_path(self, record: ArtifactRecord, *, ttl_seconds: int = 900) -> str:
        expires = int((self._clock.now() + timedelta(seconds=ttl_seconds)).timestamp())
        signature = self._signature(record.run_id, record.artifact_id, expires)
        query = urlencode({"expires": expires, "signature": signature})
        return f"/v1/runs/{record.run_id}/artifacts/{record.artifact_id}?{query}"

    def read_signed(
        self, *, run_id: UUID, artifact_id: UUID, expires: int, signature: str
    ) -> tuple[ArtifactRecord, bytes]:
        expected = self._signature(run_id, artifact_id, expires)
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("artifact signature is invalid")
        if int(self._clock.now().timestamp()) >= expires:
            raise PermissionError("artifact URL has expired")
        record = self._read_metadata(self._object_key(run_id, artifact_id, "json"))
        if record.run_id != run_id or record.artifact_id != artifact_id:
            raise PermissionError("artifact scope does not match signed URL")
        response = self._client.get_object(
            Bucket=self._bucket,
            Key=self._object_key(run_id, artifact_id, "png"),
        )
        content = response["Body"].read()
        if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != record.sha256:
            raise RuntimeError("artifact content hash does not match metadata")
        return record, content

    def cleanup_expired(self) -> int:
        deleted = 0
        for key in self._list_keys(self._base_prefix()):
            if not key.endswith(".json"):
                continue
            record = self._read_metadata(key)
            if self._clock.now() < record.expires_at:
                continue
            self._client.delete_objects(
                Bucket=self._bucket,
                Delete={
                    "Objects": [
                        {"Key": key},
                        {"Key": self._object_key(record.run_id, record.artifact_id, "png")},
                    ],
                    "Quiet": True,
                },
            )
            deleted += 1
        return deleted

    def _read_metadata(self, key: str) -> ArtifactRecord:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        content = response["Body"].read()
        if not isinstance(content, bytes):
            raise RuntimeError("S3 metadata body is not bytes")
        return _deserialize_record(content)

    def _list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            request: dict[str, object] = {"Bucket": self._bucket, "Prefix": prefix}
            if token is not None:
                request["ContinuationToken"] = token
            response = self._client.list_objects_v2(**request)
            for item in response.get("Contents", []):
                key = item.get("Key")
                if isinstance(key, str):
                    keys.append(key)
            if not response.get("IsTruncated"):
                return keys
            raw_token = response.get("NextContinuationToken")
            if not isinstance(raw_token, str):
                raise RuntimeError("S3 listing omitted continuation token")
            token = raw_token

    def _base_prefix(self) -> str:
        return f"{self._prefix}/" if self._prefix else ""

    def _object_key(self, run_id: UUID, artifact_id: UUID, suffix: str) -> str:
        return f"{self._base_prefix()}{run_id}/{artifact_id}.{suffix}"

    def _signature(self, run_id: UUID, artifact_id: UUID, expires: int) -> str:
        payload = f"{run_id}:{artifact_id}:{expires}".encode()
        return hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest()
