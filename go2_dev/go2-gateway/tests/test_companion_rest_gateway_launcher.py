from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rest_gateway_launcher_uses_the_same_single_writer_lock() -> None:
    console = (ROOT / "scripts/start_go2_companion_real.sh").read_text(
        encoding="utf-8"
    )
    gateway = (ROOT / "scripts/start_go2_companion_gateway_real.sh").read_text(
        encoding="utf-8"
    )

    lock = "/tmp/go2_companion_motion_writer.lock"
    assert lock in console
    assert lock in gateway
    assert "flock -n 9" in gateway


def test_rest_gateway_launcher_is_single_worker_and_starts_idle() -> None:
    gateway = (ROOT / "scripts/start_go2_companion_gateway_real.sh").read_text(
        encoding="utf-8"
    )

    assert "python3 -m uvicorn app.main:app" in gateway
    assert "--port 8090" in gateway
    assert "--workers 1" in gateway
    assert "companion/start" not in gateway
    assert "SportClient" not in gateway
    assert "GO2_COMPANION_CONFIG" in gateway
    assert "configs/companion_follow_real.yaml" in gateway


def test_powershell_launcher_exposes_explicit_rest_gateway_switch() -> None:
    launcher = (ROOT / "scripts/Start-Go2CompanionReal.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch]$RestGateway" in launcher
    assert '"start_go2_companion_gateway_real.sh"' in launcher
    assert '"start_go2_companion_real.sh"' in launcher

