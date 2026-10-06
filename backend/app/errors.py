"""业务规则违例：携带错误码与 HTTP 状态，API 层统一转成 JSON 响应。"""
from __future__ import annotations


class BusinessError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400,
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
