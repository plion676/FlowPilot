"""Safe API error types and response models."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status_code: int
    retryable: bool = False


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorBody
