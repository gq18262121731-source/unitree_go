from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompanionLifecycleError(RuntimeError):
    code: str
    message: str
    http_status: int = 409

    def __str__(self) -> str:
        return self.message

