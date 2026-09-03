from __future__ import annotations

from pydantic import BaseModel


class OkResponse(BaseModel):
    success: bool = True
    data: dict


class ErrorResponse(BaseModel):
    success: bool = False
    code: str
    message: str
