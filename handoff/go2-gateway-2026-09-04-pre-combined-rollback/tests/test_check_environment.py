from __future__ import annotations

import sys

import pytest

from scripts import check_environment


def _patch_environment_checks(monkeypatch, *, sdk_ok: bool, iface_ok: bool, route_ok: bool, ping_ok: bool = False) -> None:
    module_results = {
        "unitree_sdk2py": (sdk_ok, "/sdk.py" if sdk_ok else ""),
        "cv2": (True, "/cv2.py"),
        "numpy": (True, "/numpy.py"),
    }
    monkeypatch.setattr(check_environment, "has_module", lambda name: module_results[name])
    monkeypatch.setattr(
        check_environment,
        "network_interface_check",
        lambda iface: check_environment.Check("network_interface", iface_ok, "iface"),
    )
    monkeypatch.setattr(
        check_environment,
        "route_check",
        lambda robot_ip: check_environment.Check("route_to_robot", route_ok, "route"),
    )
    monkeypatch.setattr(
        check_environment,
        "ping_check",
        lambda robot_ip, iface: check_environment.Check("ping_robot", ping_ok, "ping"),
    )


def test_check_environment_allows_warnings_outside_strict_real(monkeypatch, capsys):
    _patch_environment_checks(monkeypatch, sdk_ok=False, iface_ok=False, route_ok=False)
    monkeypatch.setenv("GO2_MODE", "mock")
    monkeypatch.setattr(sys, "argv", ["check_environment.py"])

    check_environment.main()

    output = capsys.readouterr().out
    assert "[WARN] unitree_sdk2py" in output
    assert "[WARN] network_interface" in output
    assert "warnings are allowed outside strict real mode" in output


def test_check_environment_strict_real_fails_required_checks(monkeypatch, capsys):
    _patch_environment_checks(monkeypatch, sdk_ok=False, iface_ok=False, route_ok=True)
    monkeypatch.setenv("GO2_MODE", "mock")
    monkeypatch.setattr(sys, "argv", ["check_environment.py", "--strict-real"])

    with pytest.raises(SystemExit) as exc_info:
        check_environment.main()

    assert "FAILED required checks: unitree_sdk2py, network_interface" in str(exc_info.value)
    output = capsys.readouterr().out
    assert "[FAIL] unitree_sdk2py required" in output
    assert "[FAIL] network_interface required" in output
    assert "[OK] route_to_robot required" in output


def test_check_environment_real_mode_enables_strict_checks(monkeypatch):
    _patch_environment_checks(monkeypatch, sdk_ok=True, iface_ok=True, route_ok=False)
    monkeypatch.setenv("GO2_MODE", "real")
    monkeypatch.setattr(sys, "argv", ["check_environment.py"])

    with pytest.raises(SystemExit) as exc_info:
        check_environment.main()

    assert "FAILED required checks: route_to_robot" in str(exc_info.value)


def test_check_environment_require_ping_makes_ping_fatal(monkeypatch):
    _patch_environment_checks(monkeypatch, sdk_ok=True, iface_ok=True, route_ok=True, ping_ok=False)
    monkeypatch.setenv("GO2_MODE", "mock")
    monkeypatch.setattr(sys, "argv", ["check_environment.py", "--require-ping"])

    with pytest.raises(SystemExit) as exc_info:
        check_environment.main()

    assert "FAILED required checks: ping_robot" in str(exc_info.value)
