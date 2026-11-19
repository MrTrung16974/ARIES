"""Lỗi nghiệp vụ dùng chung. Tầng API ánh xạ chúng sang HTTP status, không lộ chi tiết nội bộ."""

from __future__ import annotations


class AppError(Exception):
    """Lỗi gốc của ứng dụng."""

    code: str = "app_error"
    status_code: int = 500
    public_message: str = "Đã xảy ra lỗi."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.public_message)
        self.message = message or self.public_message


class UnauthorizedError(AppError):
    code = "unauthorized"
    status_code = 401
    public_message = "Chưa xác thực hoặc token không hợp lệ."


class ForbiddenError(AppError):
    code = "forbidden"
    status_code = 403
    public_message = "Không có quyền thực hiện thao tác này."


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404
    public_message = "Không tìm thấy tài nguyên."


class ConflictError(AppError):
    code = "conflict"
    status_code = 409
    public_message = "Xung đột dữ liệu."


class ValidationError(AppError):
    code = "validation_error"
    status_code = 422
    public_message = "Dữ liệu không hợp lệ."
