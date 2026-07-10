import json
from pathlib import Path

from trust_contracts import TaskContract


def test_clean_contract_golden_hash_matches_typescript_fixture() -> None:
    fixture_path = (
        Path(__file__).parents[3] / "packages/contracts/fixtures/task-contract.clean.v1.json"
    )
    contract = TaskContract.model_validate(json.loads(fixture_path.read_text()))
    assert contract.content_hash == (
        "0ba3ced2d02ea0d2ce6fdd1f5c1a042aa204df5b2a84f3e9a94281ca5722ceb1"
    )
