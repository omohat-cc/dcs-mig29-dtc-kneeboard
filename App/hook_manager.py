"""DCS Lua hook installation and version management.

The utility ships a bundled Lua hook (``hook_template.lua``) that DCS loads on
startup. This module installs that hook into the player's Saved Games folder and
keeps it current (technical spec, sections 2 and 3):

    <saved_games>\\Scripts\\Hooks\\dtc_kneeboard_hook.lua

On startup the app calls :func:`install_or_update_hook`, which:

* creates ``Scripts/Hooks/`` if it does not exist,
* installs the hook if it is missing,
* compares the version comment on line 1 of an existing hook against the bundled
  template's version and updates the file if the installed one is older,
* otherwise leaves a current hook untouched.

The bundled version is read from the template itself (its line-1
``-- dtc_kneeboard_hook vX.Y`` comment) so the two can never drift apart. All
file I/O is wrapped and reported via a :class:`HookResult` rather than raising.

Only the Python standard library is used.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

HOOK_FILENAME = "dtc_kneeboard_hook.lua"
TEMPLATE_FILENAME = "hook_template.lua"
HOOK_SUBPATH = ("Scripts", "Hooks")

# Matches the version in the hook's line-1 comment: "-- dtc_kneeboard_hook v1.0".
_VERSION_RE = re.compile(r"dtc_kneeboard_hook\s+v([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE)

LogCallback = Callable[[str], None]


class HookAction(str, Enum):
    """What :func:`install_or_update_hook` did."""

    INSTALLED = "installed"  # hook was missing and has been written
    UPDATED = "updated"      # an older hook was replaced
    CURRENT = "current"      # the installed hook is already up to date
    ERROR = "error"          # something went wrong (see message)


@dataclass
class HookResult:
    """The outcome of an install/update attempt."""

    action: HookAction
    path: Path
    bundled_version: Optional[str]
    previous_version: Optional[str]
    message: str

    @property
    def ok(self) -> bool:
        """True unless the action errored."""
        return self.action is not HookAction.ERROR


# ---------------------------------------------------------------------------
# Bundled template access
# ---------------------------------------------------------------------------
def _resource_dir() -> Path:
    """Return the directory holding bundled resources (handles PyInstaller)."""
    frozen_dir = getattr(sys, "_MEIPASS", None)
    if frozen_dir:
        return Path(frozen_dir)
    return Path(__file__).resolve().parent


def bundled_template_path() -> Path:
    """Return the path to the bundled ``hook_template.lua``."""
    return _resource_dir() / TEMPLATE_FILENAME


def _read_text_safe(path: PathLike) -> Optional[str]:
    """Read a UTF-8 text file, returning ``None`` on any I/O error."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def read_bundled_template() -> Optional[str]:
    """Return the bundled hook template's text, or ``None`` if unreadable."""
    path = bundled_template_path()
    text = _read_text_safe(path)
    if text is None:
        logger.error("Bundled hook template not found at %s", path)
    return text


def parse_hook_version(text: Optional[str]) -> Optional[str]:
    """Extract the hook version (e.g. ``"1.0"``) from hook source text.

    The version lives in the line-1 comment ``-- dtc_kneeboard_hook v1.0``. The
    first line is checked first; if it has no match the whole text is scanned as
    a fallback. Returns ``None`` if no version comment is present.
    """
    if not text:
        return None
    lines = text.splitlines()
    if lines:
        match = _VERSION_RE.search(lines[0])
        if match:
            return match.group(1)
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def bundled_hook_version() -> Optional[str]:
    """Return the version of the bundled hook template, or ``None``."""
    return parse_hook_version(read_bundled_template())


# ---------------------------------------------------------------------------
# Install path and version comparison
# ---------------------------------------------------------------------------
def hook_install_path(saved_games_path: PathLike) -> Path:
    """Return ``<saved_games>/Scripts/Hooks/dtc_kneeboard_hook.lua``."""
    return Path(saved_games_path).joinpath(*HOOK_SUBPATH, HOOK_FILENAME)


def _version_tuple(version: Optional[str]) -> tuple[int, ...]:
    """Turn ``"1.10"`` into ``(1, 10)`` for numeric comparison."""
    if not version:
        return ()
    parts: list[int] = []
    for chunk in version.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_outdated(installed: Optional[str], bundled: Optional[str]) -> bool:
    """Return True if an installed hook should be replaced by the bundled one.

    An unversioned or unparseable installed hook is treated as outdated so it
    gets refreshed. If the bundled version itself is unknown, nothing is done.
    """
    if bundled is None:
        return False
    if installed is None:
        return True
    return _version_tuple(installed) < _version_tuple(bundled)


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------
def _write_atomic(path: Path, text: str) -> bool:
    """Write ``text`` to ``path`` via a temp file + rename. Never raises."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
        return True
    except OSError as exc:
        logger.error("Atomic write to %s failed: %s", path, exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def install_or_update_hook(
    saved_games_path: PathLike, *, log: Optional[LogCallback] = None
) -> HookResult:
    """Install the DCS hook, or update it if the installed copy is outdated.

    Creates ``Scripts/Hooks/`` if needed, writes the bundled template when no
    hook is present, replaces an older hook with the bundled one, and leaves a
    current hook untouched.

    Args:
        saved_games_path: The DCS Saved Games directory.
        log: Optional callback receiving a human-readable status message (the
            same text is also sent to the module logger).

    Returns:
        A :class:`HookResult` describing what happened. Never raises.
    """
    emit = log or (lambda _message: None)

    def report(action: HookAction, message: str, *,
               bundled: Optional[str] = None, previous: Optional[str] = None) -> HookResult:
        level = logging.ERROR if action is HookAction.ERROR else logging.INFO
        logger.log(level, message)
        emit(message)
        return HookResult(action, dest, bundled, previous, message)

    dest = hook_install_path(saved_games_path)

    template = read_bundled_template()
    if template is None:
        return report(
            HookAction.ERROR,
            f"Bundled hook template missing at {bundled_template_path()}; cannot install hook.",
        )

    bundled_version = parse_hook_version(template)
    if bundled_version is None:
        return report(
            HookAction.ERROR,
            "Bundled hook template has no version comment on line 1; refusing to install.",
        )

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return report(
            HookAction.ERROR,
            f"Could not create hook directory {dest.parent}: {exc}",
            bundled=bundled_version,
        )

    if not dest.exists():
        if _write_atomic(dest, template):
            return report(
                HookAction.INSTALLED,
                f"Installed DCS hook script to {dest}",
                bundled=bundled_version,
            )
        return report(
            HookAction.ERROR,
            f"Failed to write DCS hook to {dest}",
            bundled=bundled_version,
        )

    installed_version = parse_hook_version(_read_text_safe(dest))
    if is_outdated(installed_version, bundled_version):
        if _write_atomic(dest, template):
            return report(
                HookAction.UPDATED,
                f"Updated DCS hook script to v{bundled_version} "
                f"(was v{installed_version or 'unknown'})",
                bundled=bundled_version,
                previous=installed_version,
            )
        return report(
            HookAction.ERROR,
            f"Failed to update DCS hook at {dest}",
            bundled=bundled_version,
            previous=installed_version,
        )

    return report(
        HookAction.CURRENT,
        f"DCS hook script is current (v{installed_version}); no action.",
        bundled=bundled_version,
        previous=installed_version,
    )


# ---------------------------------------------------------------------------
# CLI: install/update into a given Saved Games path
# ---------------------------------------------------------------------------
def _main() -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Install or update the DCS DTC kneeboard hook.")
    parser.add_argument("saved_games", help="Path to the DCS Saved Games directory.")
    args = parser.parse_args()

    result = install_or_update_hook(args.saved_games, log=print)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
