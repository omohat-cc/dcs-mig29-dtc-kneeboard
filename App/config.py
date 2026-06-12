"""Configuration loading, saving and path auto-detection for the DTC utility.

The app keeps a single ``config.json`` next to the executable holding the three
DCS paths it needs plus the installed hook version (technical spec, section 5):

    {
        "version": 1,
        "dcs_install_path": "D:\\\\DCS World",
        "dcs_saved_games_path": "C:\\\\Users\\\\<user>\\\\Saved Games\\\\DCS",
        "dcs_temp_path": "C:\\\\Users\\\\<user>\\\\AppData\\\\Local\\\\Temp\\\\DCS",
        "hook_version": "1.0"
    }

On first run the three paths are auto-detected:

* **DCS install** - Windows registry (``HKLM\\SOFTWARE\\Eagle Dynamics\\DCS World``),
  then a scan of common install roots, validated by the presence of
  ``bin\\DCS.exe``.
* **Saved Games** - ``%USERPROFILE%\\Saved Games\\DCS\\``, validated as a directory.
* **Temp** - ``%LOCALAPPDATA%\\Temp\\DCS\\``, validated as a directory (it may not
  exist until DCS has run once).

Detection is the only Windows-specific part; the registry read is guarded by a
platform check and the rest uses :mod:`pathlib`, so this module imports and runs
on macOS (where detection simply finds nothing and reports each path as needing
manual entry).

Only the Python standard library is used.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

CONFIG_VERSION = 1
CONFIG_FILENAME = "config.json"

# Common DCS install roots to scan if the registry lookup fails (spec section 5).
_DCS_COMMON_INSTALL_PATHS = (
    r"C:\Program Files\Eagle Dynamics\DCS World",
    r"D:\DCS World",
    r"E:\DCS World",
    r"D:\Games\DCS World",
)

# HKLM registry subkeys to try, stable release first then Open Beta.
_DCS_REGISTRY_SUBKEYS = (
    r"SOFTWARE\Eagle Dynamics\DCS World",
    r"SOFTWARE\Eagle Dynamics\DCS World OpenBeta",
)

# Saved Games folder names, stable first then the Open Beta variant.
_SAVED_GAMES_NAMES = ("DCS", "DCS.openbeta")


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------
@dataclass
class AppConfig:
    """The persisted application configuration (mirrors ``config.json``)."""

    version: int = CONFIG_VERSION
    dcs_install_path: Optional[str] = None
    dcs_saved_games_path: Optional[str] = None
    dcs_temp_path: Optional[str] = None
    hook_version: Optional[str] = None

    def to_json_dict(self) -> dict:
        """Return a plain dict in the documented ``config.json`` key order."""
        return {
            "version": self.version,
            "dcs_install_path": self.dcs_install_path,
            "dcs_saved_games_path": self.dcs_saved_games_path,
            "dcs_temp_path": self.dcs_temp_path,
            "hook_version": self.hook_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        """Build an :class:`AppConfig` from a loaded JSON dict, tolerating gaps.

        Unknown keys are ignored and missing keys fall back to defaults, so an
        older or partially hand-edited ``config.json`` still loads cleanly.
        """
        def _opt_str(value: object) -> Optional[str]:
            if value is None:
                return None
            text = str(value).strip()
            return text or None

        try:
            version = int(data.get("version", CONFIG_VERSION))
        except (TypeError, ValueError):
            version = CONFIG_VERSION

        return cls(
            version=version,
            dcs_install_path=_opt_str(data.get("dcs_install_path")),
            dcs_saved_games_path=_opt_str(data.get("dcs_saved_games_path")),
            dcs_temp_path=_opt_str(data.get("dcs_temp_path")),
            hook_version=_opt_str(data.get("hook_version")),
        )


# ---------------------------------------------------------------------------
# Path-status reporting (used by the GUI to decide which Browse popups to show)
# ---------------------------------------------------------------------------
@dataclass
class PathStatus:
    """The outcome of resolving one configured path."""

    key: str               # AppConfig attribute name, e.g. "dcs_install_path"
    label: str             # human label, e.g. "DCS install"
    value: Optional[str]   # the resolved value, if any
    valid: bool            # does the value pass validation
    auto_detected: bool    # was it found by auto-detection this run

    @property
    def needs_manual(self) -> bool:
        """True if the GUI should prompt the user to pick this path manually."""
        return not self.valid


@dataclass
class DetectionReport:
    """The result of auto-detecting all configured paths."""

    statuses: list[PathStatus]

    @property
    def missing(self) -> list[PathStatus]:
        """Paths that failed auto-detection and need manual entry."""
        return [status for status in self.statuses if status.needs_manual]

    @property
    def all_found(self) -> bool:
        """True if every path validated."""
        return not self.missing

    def by_key(self, key: str) -> Optional[PathStatus]:
        """Return the status for a given config key, or ``None``."""
        for status in self.statuses:
            if status.key == key:
                return status
        return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_install_path(path: Optional[PathLike]) -> bool:
    """Return True if ``path`` looks like a DCS install (has ``bin/DCS.exe``)."""
    if not path:
        return False
    try:
        return (Path(path) / "bin" / "DCS.exe").is_file()
    except OSError:
        return False


def validate_saved_games_path(path: Optional[PathLike]) -> bool:
    """Return True if ``path`` is an existing directory."""
    if not path:
        return False
    try:
        return Path(path).is_dir()
    except OSError:
        return False


# The temp directory uses the same rule as Saved Games (an existing directory),
# but is aliased for readability at call sites.
validate_temp_path = validate_saved_games_path


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------
def _detect_install_from_registry() -> Optional[Path]:
    """Read the DCS install path from the Windows registry, or ``None``.

    Returns ``None`` on any non-Windows platform or if the key/value is absent.
    Both 64- and 32-bit registry views are tried for each candidate subkey.
    """
    if platform.system() != "Windows":
        return None
    try:
        import winreg  # noqa: PLC0415 - Windows-only, imported lazily under guard
    except ImportError:
        return None

    views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for subkey in _DCS_REGISTRY_SUBKEYS:
        for view in views:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE, subkey, 0, winreg.KEY_READ | view
                ) as key:
                    value, _ = winreg.QueryValueEx(key, "Path")
            except OSError:
                continue
            if value and validate_install_path(value):
                logger.info("DCS install found via registry (%s): %s", subkey, value)
                return Path(value)
    return None


def detect_dcs_install() -> Optional[Path]:
    """Auto-detect the DCS install directory.

    Tries the Windows registry first, then scans the common install roots,
    validating each by the presence of ``bin/DCS.exe``. Returns ``None`` if
    nothing validates (the GUI then prompts for manual entry).
    """
    from_registry = _detect_install_from_registry()
    if from_registry is not None:
        return from_registry
    for candidate in _DCS_COMMON_INSTALL_PATHS:
        if validate_install_path(candidate):
            logger.info("DCS install found by path scan: %s", candidate)
            return Path(candidate)
    return None


def _user_profile_dir() -> Path:
    """Return the user profile directory (``%USERPROFILE%`` or the home dir)."""
    profile = os.environ.get("USERPROFILE")
    return Path(profile) if profile else Path.home()


def detect_saved_games() -> Optional[Path]:
    """Auto-detect ``%USERPROFILE%\\Saved Games\\DCS``, validated as a directory."""
    base = _user_profile_dir() / "Saved Games"
    for name in _SAVED_GAMES_NAMES:
        candidate = base / name
        if validate_saved_games_path(candidate):
            logger.info("DCS Saved Games found: %s", candidate)
            return candidate
    return None


def _local_appdata_dir() -> Optional[Path]:
    """Return ``%LOCALAPPDATA%`` (or its Windows default), else ``None``."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local)
    if platform.system() == "Windows":
        return _user_profile_dir() / "AppData" / "Local"
    return None


def detect_temp() -> Optional[Path]:
    """Auto-detect ``%LOCALAPPDATA%\\Temp\\DCS``, validated as a directory.

    This directory may not exist until DCS has run at least once, in which case
    detection returns ``None`` and the GUI prompts for manual entry.
    """
    base = _local_appdata_dir()
    if base is None:
        return None
    candidate = base / "Temp" / "DCS"
    if validate_temp_path(candidate):
        logger.info("DCS temp directory found: %s", candidate)
        return candidate
    return None


def _safe_detect(detector: Callable[[], Optional[Path]], label: str) -> Optional[Path]:
    """Run a detector, swallowing and logging any unexpected failure."""
    try:
        return detector()
    except Exception as exc:  # noqa: BLE001 - detection must never crash startup
        logger.exception("Auto-detection for %s failed: %s", label, exc)
        return None


def _resolve_one(
    config: AppConfig,
    key: str,
    label: str,
    validator: Callable[[Optional[PathLike]], bool],
    detector: Callable[[], Optional[Path]],
) -> PathStatus:
    """Resolve a single path: keep a valid existing value, else auto-detect."""
    current = getattr(config, key)
    if current and validator(current):
        return PathStatus(key, label, current, valid=True, auto_detected=False)

    detected = _safe_detect(detector, label)
    if detected is not None:
        setattr(config, key, str(detected))
        return PathStatus(key, label, str(detected), valid=True, auto_detected=True)

    # Not found. Leave any (invalid) existing value in place so the GUI can show
    # it as a starting point, and flag that manual entry is required.
    return PathStatus(key, label, current, valid=False, auto_detected=False)


def autodetect_paths(config: AppConfig) -> DetectionReport:
    """Fill in any missing or invalid paths on ``config`` via auto-detection.

    A path that is already present and valid is kept untouched. Mutates
    ``config`` in place and returns a :class:`DetectionReport` describing each
    path's final status (including which still need manual entry).
    """
    statuses = [
        _resolve_one(config, "dcs_install_path", "DCS install",
                     validate_install_path, detect_dcs_install),
        _resolve_one(config, "dcs_saved_games_path", "DCS Saved Games",
                     validate_saved_games_path, detect_saved_games),
        _resolve_one(config, "dcs_temp_path", "DCS temp directory",
                     validate_temp_path, detect_temp),
    ]
    report = DetectionReport(statuses=statuses)
    logger.info(
        "Path auto-detection: %d/%d found (%s need manual entry).",
        len(statuses) - len(report.missing), len(statuses),
        ", ".join(s.label for s in report.missing) or "none",
    )
    return report


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------
def app_directory() -> Path:
    """Return the directory the app runs from (next to the exe when frozen).

    For a PyInstaller ``--onefile`` build this is the directory containing the
    executable; in development it is this module's directory.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_config_path() -> Path:
    """Return the default ``config.json`` path (next to the executable)."""
    return app_directory() / CONFIG_FILENAME


def load_config(path: Optional[PathLike] = None) -> AppConfig:
    """Load ``config.json`` into an :class:`AppConfig`.

    Args:
        path: Explicit config path; defaults to :func:`default_config_path`.

    Returns:
        The loaded config, or a default (all-empty) config if the file is
        missing, unreadable, or not valid JSON. Never raises.
    """
    config_path = Path(path) if path else default_config_path()
    if not config_path.is_file():
        logger.info("No config at %s; starting with empty defaults.", config_path)
        return AppConfig()

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Could not read config %s: %s; using defaults.", config_path, exc)
        return AppConfig()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("Config %s is not valid JSON: %s; using defaults.", config_path, exc)
        return AppConfig()

    if not isinstance(data, dict):
        logger.error("Config %s is not a JSON object; using defaults.", config_path)
        return AppConfig()

    logger.info("Loaded config from %s", config_path)
    return AppConfig.from_dict(data)


def save_config(config: AppConfig, path: Optional[PathLike] = None) -> bool:
    """Write ``config`` to ``config.json`` atomically.

    Args:
        config: The configuration to persist.
        path: Explicit config path; defaults to :func:`default_config_path`.

    Returns:
        True on success, False if the file could not be written. Never raises.
    """
    config_path = Path(path) if path else default_config_path()
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(config.to_json_dict(), indent=4), encoding="utf-8"
        )
        os.replace(tmp_path, config_path)  # atomic publish
        logger.info("Saved config to %s", config_path)
        return True
    except OSError as exc:
        logger.error("Could not save config to %s: %s", config_path, exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load_and_autodetect(
    path: Optional[PathLike] = None, *, save: bool = True
) -> tuple[AppConfig, DetectionReport]:
    """Load the config, auto-detect any missing paths, and (optionally) save.

    This is the convenience entry point for first-run startup: it loads
    ``config.json`` (or empty defaults), fills in whatever paths it can detect,
    writes the result back when anything changed or the file did not yet exist,
    and returns both the config and a report of what still needs manual entry.

    Args:
        path: Explicit config path; defaults to :func:`default_config_path`.
        save: Whether to persist newly detected paths back to disk.

    Returns:
        A ``(config, report)`` tuple.
    """
    config_path = Path(path) if path else default_config_path()
    existed = config_path.is_file()
    config = load_config(config_path)
    report = autodetect_paths(config)

    if save and (not existed or any(s.auto_detected for s in report.statuses)):
        save_config(config, config_path)

    return config, report


# ---------------------------------------------------------------------------
# CLI: print detection results (handy on the Windows target)
# ---------------------------------------------------------------------------
def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    config, report = load_and_autodetect(save=False)
    print(f"\nConfig (version {config.version}):")
    for status in report.statuses:
        mark = "OK " if status.valid else "?? "
        source = " (auto-detected)" if status.auto_detected else ""
        print(f"  [{mark}] {status.label}: {status.value or '(not found)'}{source}")
    print(f"\nHook version: {config.hook_version or '(none)'}")
    if report.missing:
        print("\nNeeds manual entry:")
        for status in report.missing:
            print(f"  - {status.label}")
    return 0 if report.all_found else 1


if __name__ == "__main__":
    raise SystemExit(_main())
