"""HTTP surface for the isolated post-run evaluator."""

from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status

from trust_oracle import __version__
from trust_oracle.config import OracleSettings, get_settings
from trust_oracle.scoring import GroundTruthSnapshot, OracleResult, score_snapshot

app = FastAPI(
    title="Trust Runtime Sealed Oracle",
    version=__version__,
    docs_url=None,
    redoc_url=None,
)


def require_operator(
    settings: Annotated[OracleSettings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = f"Bearer {settings.operator_token.get_secret_value()}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="operator required")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "trust-oracle", "version": __version__}


@app.post(
    "/internal/v1/score",
    response_model=OracleResult,
    dependencies=[Depends(require_operator)],
)
async def score(snapshot: GroundTruthSnapshot) -> OracleResult:
    """Development-only shape; production loads snapshot by completed run ID."""

    return score_snapshot(snapshot)
