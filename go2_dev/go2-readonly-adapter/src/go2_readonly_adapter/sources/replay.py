from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..events import SensorEvent


class JsonlReplaySource:
    """Deterministic offline source used while the robot is powered off."""

    source_name = "replay"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def events(self, duration_seconds: float) -> Iterable[SensorEvent]:
        del duration_seconds
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{self.path}:{line_number}: invalid JSON: {exc}"
                    ) from exc
                yield SensorEvent.from_mapping(value)

