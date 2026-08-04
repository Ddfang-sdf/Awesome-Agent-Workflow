from __future__ import annotations


class EvalError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        kind: str = "invalid",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.kind = kind


class InfrastructureError(EvalError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=500, kind="infra_error")
