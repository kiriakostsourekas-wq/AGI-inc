#!/usr/bin/env python3
"""Run deterministic repository-level security boundary checks.

This is a focused static guard, not a substitute for runtime penetration tests,
browser egress tests, secret scanning in CI, or the live safety-gate matrix.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRECTORIES: Final = frozenset(
    {
        ".git",
        ".next",
        ".venv",
        "node_modules",
        "coverage",
        "dist",
        "build",
        "artifacts",
        "test-results",
        "playwright-report",
        "__pycache__",
    }
)
SCANNED_SUFFIXES: Final = frozenset(
    {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yml", ".yaml", ".sql", ".sh"}
)
SECRET_PATTERNS: Final = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "github_token": re.compile(r"\bgh[opsu]_[A-Za-z0-9]{30,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"),
}
CLIENT_FORBIDDEN_NAMES: Final = frozenset(
    {
        "OPENAI_API_KEY",
        "APPROVAL_HMAC_SECRET",
        "ARTIFACT_SIGNING_SECRET",
        "DEMO_PAYMENT_SECRET",
        "DATABASE_URL",
        "ORACLE_DATABASE_URL",
        "EVALUATION_OPERATOR_TOKEN",
        "OBJECT_STORAGE_SECRET_KEY",
    }
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def authored_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.name in {"pnpm-lock.yaml", "uv.lock"}:
            continue
        if path.suffix in SCANNED_SUFFIXES or path.name in {".env.example", "Makefile"}:
            files.append(path)
    return sorted(files)


def scan_secret_patterns(root: Path = ROOT) -> Check:
    findings: list[str] = []
    for path in authored_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(root)}:{name}")
    if findings:
        return Check("secret_patterns", "FAIL", ", ".join(findings))
    return Check(
        "secret_patterns",
        "PASS",
        f"no credential-shaped values in {len(authored_files(root))} authored files",
    )


def scan_client_secret_references(root: Path = ROOT) -> Check:
    findings: list[str] = []
    for path in authored_files(root):
        if path.suffix not in {".ts", ".tsx", ".js", ".mjs"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        first_statement = text.lstrip().splitlines()[0] if text.strip() else ""
        if first_statement not in {
            '"use client";',
            "'use client';",
            '"use client"',
            "'use client'",
        }:
            continue
        for name in CLIENT_FORBIDDEN_NAMES:
            if name in text:
                findings.append(f"{path.relative_to(root)}:{name}")
    if findings:
        return Check("client_secret_references", "FAIL", ", ".join(findings))
    return Check(
        "client_secret_references",
        "PASS",
        "no server-only secret variable is referenced by a client module",
    )


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def check_egress_allowlists(root: Path = ROOT) -> Check:
    env = _env_values(root / ".env.example")
    browser_raw = env.get("BROWSER_ALLOWED_ORIGINS", "")
    service_raw = env.get("SERVICE_ALLOWED_HOSTS", "")
    browser = [value.strip() for value in browser_raw.split(",") if value.strip()]
    service = [value.strip().lower() for value in service_raw.split(",") if value.strip()]
    errors: list[str] = []
    browser_hosts: set[str] = set()
    if not browser:
        errors.append("browser allowlist is empty")
    for origin in browser:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            errors.append(f"invalid browser origin {origin}")
            continue
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            errors.append(f"browser allowlist entry is not an exact origin: {origin}")
        if parsed.scheme == "http" and not (
            parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost")
        ):
            errors.append(f"non-local development origin lacks HTTPS: {origin}")
        if "oracle" in parsed.hostname or parsed.hostname in {"api.openai.com"}:
            errors.append(f"privileged/service host appears in browser allowlist: {origin}")
        browser_hosts.add(parsed.hostname)
    if not service:
        errors.append("service allowlist is empty")
    for hostname in service:
        parsed = urlsplit(f"//{hostname}")
        if (
            parsed.hostname != hostname
            or parsed.port is not None
            or any(token in hostname for token in ("/", "?", "#", "@"))
        ):
            errors.append(f"service entry must be a bare hostname: {hostname}")
        if hostname in browser_hosts or hostname == "localhost" or hostname.endswith(".localhost"):
            errors.append(f"browser sandbox host appears in service allowlist: {hostname}")
    if len(browser) != len(set(browser)) or len(service) != len(set(service)):
        errors.append("allowlist contains duplicates")
    if errors:
        return Check("egress_allowlists", "FAIL", "; ".join(errors))
    return Check(
        "egress_allowlists",
        "PASS",
        f"{len(browser)} exact browser origins and {len(service)} separate service host validated",
    )


def check_prompt_injection_corpus(root: Path = ROOT) -> Check:
    path = root / "evals" / "cases" / "prompt-injection-corpus.v1.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Check("prompt_injection_corpus", "FAIL", str(exc))
    cases = payload.get("cases", [])
    seeds = {case.get("seed") for case in cases if isinstance(case, dict)}
    attack_classes = {case.get("attackClass") for case in cases if isinstance(case, dict)}
    expected_seeds = set(range(2101, 2106))
    if seeds != expected_seeds or len(attack_classes) != 5:
        return Check(
            "prompt_injection_corpus",
            "FAIL",
            f"expected seeds {sorted(expected_seeds)} and five distinct attack classes",
        )
    if any(
        not isinstance(case.get("expectedControls"), list) or not case["expectedControls"]
        for case in cases
    ):
        return Check("prompt_injection_corpus", "FAIL", "each corpus row needs expected controls")
    return Check(
        "prompt_injection_corpus",
        "PASS",
        "five predeclared authority, origin, budget, hidden-tool, and secret-exfiltration attacks",
    )


def check_security_test_map(root: Path = ROOT) -> Check:
    path = root / "evals" / "evidence" / "security-test-map.v1.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Check("security_test_map", "FAIL", str(exc))
    errors: list[str] = []
    controls = payload.get("controls", [])
    for control in controls:
        test_path = root / control["testFile"]
        if not test_path.is_file():
            errors.append(f"missing {control['testFile']}")
            continue
        text = test_path.read_text(encoding="utf-8")
        if control["testName"] not in text:
            errors.append(f"missing {control['testName']} in {control['testFile']}")
    if errors:
        return Check("security_test_map", "FAIL", "; ".join(errors))
    return Check(
        "security_test_map", "PASS", f"{len(controls)} runtime/TypeScript behavioral tests mapped"
    )


def run_audit(root: Path = ROOT) -> list[Check]:
    return [
        scan_secret_patterns(root),
        scan_client_secret_references(root),
        check_egress_allowlists(root),
        check_prompt_injection_corpus(root),
        check_security_test_map(root),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic Trust Runtime static security checks"
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    checks = run_audit()
    if args.json:
        print(json.dumps({"checks": [asdict(check) for check in checks]}, indent=2))
    else:
        for check in checks:
            print(f"{check.status:4} {check.name}: {check.detail}")
    return 1 if any(check.status == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
