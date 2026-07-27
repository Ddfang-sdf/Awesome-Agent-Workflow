from __future__ import annotations

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException

from . import __version__
from .config import Settings
from .contracts import AttributionRequest, AttributionResult
from .engine import AttributionEngine, MockAttributionEngine


def create_app(
    *,
    settings: Settings | None = None,
    engine: AttributionEngine | None = None,
) -> FastAPI:
    settings = settings or Settings()
    engine = engine or MockAttributionEngine()
    app = FastAPI(
        title="AAW Attribution Service",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )

    def authorize(authorization: str | None = Header(default=None)) -> None:
        if settings.api_token is None:
            return
        expected = f"Bearer {settings.api_token.get_secret_value()}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid attribution service token")

    @app.get("/health/live", include_in_schema=False)
    def liveness():
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready", include_in_schema=False)
    def readiness():
        return {"status": "ok", "engine": type(engine).__name__}

    @app.post(
        "/api/v1/attributions",
        response_model=AttributionResult,
        dependencies=[Depends(authorize)],
    )
    def attribute(request: AttributionRequest) -> AttributionResult:
        return engine.attribute(request)

    return app


app = create_app()
