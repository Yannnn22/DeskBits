"""统一错误响应格式。

所有 HTTP 错误都返回:

    {"error": {"code": str, "message": str, "details": {}}}

WebSocket error 事件保持同构: {"type": "error", "payload": {"code", "message"}}。
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """带机器可读 code 的业务错误。"""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Any:
        details: dict[str, Any] = {}
        code = str(exc.detail)
        message = str(exc.detail)
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code", exc.status_code))
            message = str(exc.detail.get("message", exc.status_code))
            raw_details = exc.detail.get("details")
            if isinstance(raw_details, dict):
                details = raw_details
        return _error_response(exc.status_code, code, message, details)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> Any:
        return _error_response(
            422,
            "validation_error",
            "请求参数不合法",
            {"errors": exc.errors()},
        )

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> Any:
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any],
) -> Any:
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )
