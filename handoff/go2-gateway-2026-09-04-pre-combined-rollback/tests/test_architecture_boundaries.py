from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_DIRS = [
    ROOT / "app" / "api",
    ROOT / "app" / "event",
    ROOT / "app" / "services",
    ROOT / "app" / "task_manager",
]
SDK_IMPORT_ALLOWED = {
    ROOT / "app" / "adapters" / "unitree_adapter.py",
    ROOT / "app" / "providers" / "unitree" / "dds_reader.py",
    ROOT / "app" / "providers" / "unitree" / "phase7_input_stream.py",
    ROOT / "app" / "providers" / "unitree" / "real_provider.py",
}
SCRIPT_ADAPTER_IMPORT_ALLOWED = {
    ROOT / "scripts" / "adapter_factory.py",
    ROOT / "scripts" / "probe_dds_status.py",
}


def _python_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        files.extend(item for item in path.rglob("*.py") if "__pycache__" not in item.parts)
    return sorted(files)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def test_business_layers_do_not_import_sdk_or_adapters_directly():
    offenders: list[str] = []
    for path in _python_files(BUSINESS_DIRS):
        for module in _imports(path):
            if module == "unitree_sdk2py" or module.startswith("unitree_sdk2py."):
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")
            if module == "app.adapters" or module.startswith("app.adapters."):
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")

    assert offenders == []


def test_unitree_sdk_import_is_confined_to_real_hardware_boundaries():
    offenders: list[str] = []
    for path in _python_files([ROOT / "app"]):
        if path in SDK_IMPORT_ALLOWED:
            continue
        for module in _imports(path):
            if module == "unitree_sdk2py" or module.startswith("unitree_sdk2py."):
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")

    assert offenders == []


def test_verification_scripts_do_not_import_adapters_directly():
    offenders: list[str] = []
    for path in _python_files([ROOT / "scripts"]):
        if path in SCRIPT_ADAPTER_IMPORT_ALLOWED:
            continue
        for module in _imports(path):
            if module == "unitree_sdk2py" or module.startswith("unitree_sdk2py."):
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")
            if module == "app.adapters" or module.startswith("app.adapters."):
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")

    assert offenders == []


def test_motion_and_lazy_companion_exports_import_in_clean_interpreter():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app.motion; "
                "from app.companion import CompanionLifecycleService, CompanionRuntime"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
