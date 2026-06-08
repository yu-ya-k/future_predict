from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class ForecastConflict(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def forecast_http_error(
    error: ForecastConflict,
    *,
    status_code: int = status.HTTP_409_CONFLICT,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": error.code,
            "message": error.message,
            "details": error.details,
        },
    )

