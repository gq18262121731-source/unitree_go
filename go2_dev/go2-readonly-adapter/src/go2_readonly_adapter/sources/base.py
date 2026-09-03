from __future__ import annotations

from typing import Iterable, Protocol

from ..events import SensorEvent


class ReadonlySource(Protocol):
    source_name: str

    def events(self, duration_seconds: float) -> Iterable[SensorEvent]:
        """Yield observations without creating any command or publisher surface."""

