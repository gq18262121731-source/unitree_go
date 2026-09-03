from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import requests
from pydantic import ValidationError

from backend.models.robot_readonly_telemetry_model import (
    RobotReadonlyTelemetryIntegration,
    UnitreeReadonlyStatus,
)


class RobotReadonlyTelemetryService:
    """Read a Phase 6.1 status snapshot without exposing command capabilities."""

    def __init__(
        self,
        *,
        integration_mode: Literal["mock", "unitree_readonly"] = "mock",
        snapshot_path: str = "",
        snapshot_url: str = "",
        timeout_seconds: float = 1.0,
        session: requests.Session | None = None,
    ) -> None:
        self.integration_mode = integration_mode
        self.snapshot_path = str(snapshot_path or "").strip()
        self.snapshot_url = str(snapshot_url or "").strip()
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._session = session or requests.Session()

    def snapshot(self) -> RobotReadonlyTelemetryIntegration:
        if self.integration_mode == "mock":
            return RobotReadonlyTelemetryIntegration(
                provider="mock",
                integration_mode="mock",
                source_status="mock_frozen",
            )

        try:
            payload = self._read_payload()
            readonly_status = UnitreeReadonlyStatus.model_validate(payload)
        except FileNotFoundError as exc:
            return self._failure("unavailable", "READONLY_SOURCE_NOT_FOUND", str(exc))
        except requests.exceptions.Timeout as exc:
            return self._failure("unavailable", "READONLY_SOURCE_TIMEOUT", str(exc))
        except requests.exceptions.RequestException as exc:
            return self._failure("unavailable", "READONLY_SOURCE_UNAVAILABLE", str(exc))
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            return self._failure("invalid", "READONLY_CONTRACT_INVALID", str(exc))

        return RobotReadonlyTelemetryIntegration(
            provider="unitree_readonly",
            integration_mode="unitree_readonly",
            source_status="ready",
            readonly_status=readonly_status,
        )

    def _read_payload(self) -> Any:
        if self.snapshot_url:
            response = self._session.get(self.snapshot_url, timeout=self.timeout_seconds)
            response.raise_for_status()
            return response.json()
        if self.snapshot_path:
            return json.loads(Path(self.snapshot_path).read_text(encoding="utf-8"))
        raise FileNotFoundError(
            "Unitree readonly mode requires robot_readonly_snapshot_url "
            "or robot_readonly_snapshot_path"
        )

    @staticmethod
    def _failure(
        source_status: Literal["unavailable", "invalid"],
        error_code: str,
        error_message: str,
    ) -> RobotReadonlyTelemetryIntegration:
        return RobotReadonlyTelemetryIntegration(
            provider="unitree_readonly",
            integration_mode="unitree_readonly",
            source_status=source_status,
            error_code=error_code,
            error_message=error_message,
        )
