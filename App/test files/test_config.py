"""Tests for :mod:`config` - load/save, validation and auto-detection.

Exercises the config dataclass round-trip, the path validators, the load/save
round-trip (including the corrupt/missing fallbacks), and the auto-detection
behaviour that keeps an already-valid path while flagging missing ones for
manual entry. Detection itself is verified structurally (it must run and report
three paths without crashing) since the real DCS paths only exist on the Windows
target; a fabricated install tree exercises the "keep a valid path" branch.

Self-contained (no pytest); run with:

    python3 "test files/test_config.py"
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import config  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)


class Checks:
    """Collects pass/fail results without aborting on the first failure."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        status = "PASS" if condition else "FAIL"
        self.passed += int(bool(condition))
        self.failed += int(not condition)
        suffix = f"  ({detail})" if detail else ""
        print(f"    [{status}] {label}{suffix}")
        return condition

    def summary(self) -> bool:
        total = self.passed + self.failed
        print("\n" + "=" * 70)
        print(f"CONFIG: {self.passed}/{total} checks passed, {self.failed} failed.")
        print("=" * 70)
        return self.failed == 0


def _banner(title: str) -> None:
    print("\n" + "#" * 70)
    print(f"# {title}")
    print("#" * 70)


def _fake_install(root: Path) -> Path:
    """Create a minimal valid DCS install tree (``bin/DCS.exe``) under ``root``."""
    install = root / "DCS World"
    (install / "bin").mkdir(parents=True, exist_ok=True)
    (install / "bin" / "DCS.exe").write_text("stub", encoding="utf-8")
    return install


def test_dataclass(checks: Checks) -> None:
    _banner("DATACLASS  -  round-trip and tolerant from_dict")
    cfg = config.AppConfig(
        dcs_install_path="D:\\DCS World",
        dcs_saved_games_path="C:\\sg",
        dcs_temp_path="C:\\tmp",
        hook_version="1.0",
    )
    checks.check("to_json_dict / from_dict round-trips",
                 config.AppConfig.from_dict(cfg.to_json_dict()) == cfg)

    schema_keys = {"version", "dcs_install_path", "dcs_saved_games_path",
                   "dcs_temp_path", "hook_version"}
    checks.check("to_json_dict has exactly the schema keys",
                 set(cfg.to_json_dict().keys()) == schema_keys)

    partial = config.AppConfig.from_dict({"dcs_install_path": "X", "unknown_key": 1})
    checks.check("from_dict tolerates missing + unknown keys",
                 partial.dcs_install_path == "X" and partial.dcs_saved_games_path is None
                 and partial.version == config.CONFIG_VERSION)

    blanks = config.AppConfig.from_dict({"dcs_temp_path": "   ", "hook_version": ""})
    checks.check("from_dict normalises blank strings to None",
                 blanks.dcs_temp_path is None and blanks.hook_version is None)


def test_validators(checks: Checks) -> None:
    _banner("VALIDATORS  -  install needs bin/DCS.exe; dirs must exist")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install = _fake_install(root)
        checks.check("Valid install (bin/DCS.exe present)",
                     config.validate_install_path(install))
        checks.check("Install dir without bin/DCS.exe is invalid",
                     not config.validate_install_path(root / "empty"))
        checks.check("Existing directory validates as Saved Games",
                     config.validate_saved_games_path(root))
        checks.check("Missing directory fails validation",
                     not config.validate_temp_path(root / "nope"))
        checks.check("None / empty paths fail validation",
                     not config.validate_install_path(None)
                     and not config.validate_saved_games_path(""))


def test_load_save(checks: Checks) -> None:
    _banner("LOAD / SAVE  -  round-trip and corrupt/missing fallbacks")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"

        checks.check("Missing config -> default AppConfig",
                     config.load_config(path) == config.AppConfig())

        cfg = config.AppConfig(dcs_install_path="D:\\DCS World", hook_version="1.0")
        checks.check("save_config reports success", config.save_config(cfg, path))
        checks.check("save_config wrote the file", path.is_file())
        checks.check("Saved then loaded config matches", config.load_config(path) == cfg)

        path.write_text("{ not valid json", encoding="utf-8")
        checks.check("Corrupt JSON -> default AppConfig",
                     config.load_config(path) == config.AppConfig())

        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        checks.check("Non-object JSON -> default AppConfig",
                     config.load_config(path) == config.AppConfig())


def test_autodetect(checks: Checks) -> None:
    _banner("AUTO-DETECT  -  keep valid, flag missing, never crash")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install = _fake_install(root)

        # A pre-set valid install path must be kept (not "auto-detected").
        cfg = config.AppConfig(dcs_install_path=str(install))
        report = config.autodetect_paths(cfg)
        checks.check("autodetect returns three path statuses", len(report.statuses) == 3)

        install_status = report.by_key("dcs_install_path")
        checks.check("Valid pre-set install path is kept and marked valid",
                     install_status is not None and install_status.valid
                     and not install_status.auto_detected)
        checks.check("Config install path unchanged after autodetect",
                     cfg.dcs_install_path == str(install))

        # A bogus path that cannot be detected on this machine -> needs manual.
        sg_status = report.by_key("dcs_saved_games_path")
        checks.check("Undetected Saved Games path flagged for manual entry",
                     sg_status is not None and sg_status.needs_manual)
        checks.check("report.missing lists the undetected paths",
                     all(s.needs_manual for s in report.missing))

    # load_and_autodetect must create the file on first run, even on a machine
    # where nothing is detected.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        cfg, report = config.load_and_autodetect(path)
        checks.check("load_and_autodetect wrote config.json on first run", path.is_file())
        checks.check("load_and_autodetect returns a (config, report) pair",
                     isinstance(cfg, config.AppConfig) and len(report.statuses) == 3)


def main() -> int:
    checks = Checks()
    test_dataclass(checks)
    test_validators(checks)
    test_load_save(checks)
    test_autodetect(checks)
    return 0 if checks.summary() else 1


if __name__ == "__main__":
    sys.exit(main())
