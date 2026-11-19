from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from aiagent.shared_kernel.errors import AppError

logger = logging.getLogger(__name__)


def _body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


async def _app_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        return await _unhandled(_, exc)
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    # Chỉ trả public_message cho lỗi xác thực để không lộ lý do cụ thể.
    message = exc.public_message if exc.status_code in (401, 500) else exc.message
    return JSONResponse(_body(exc.code, message), status_code=exc.status_code, headers=headers)


async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Lỗi không mong đợi", exc_info=exc)
    return JSONResponse(_body("internal_error", "Đã xảy ra lỗi."), status_code=500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(Exception, _unhandled)
