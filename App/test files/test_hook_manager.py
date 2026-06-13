"""Tests for :mod:`hook_manager` and the bundled ``hook_template.lua``.

Covers version parsing/comparison, the install/update/current decision logic
(against a throwaway Saved Games directory), directory creation, and a
structural validation of the bundled Lua template against the spec section 2
safety constraints (pcall guards, atomic write, no forbidden calls).

Self-contained (no pytest); run with:

    python3 "test files/test_hook_manager.py"
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import hook_manager as hm  # noqa: E402

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
        print(f"HOOK MANAGER: {self.passed}/{total} checks passed, {self.failed} failed.")
        print("=" * 70)
        return self.failed == 0


def _banner(title: str) -> None:
    print("\n" + "#" * 70)
    print(f"# {title}")
    print("#" * 70)


def _strip_lua_comments(text: str) -> str:
    """Drop Lua line comments so token checks see only executable code.

    The template documents the calls it deliberately avoids in its header
    comment, so a naive substring search would match that documentation. No
    ``--`` appears inside a string literal in the template, so cutting each line
    at its first ``--`` cleanly removes both full-line and inline comments.
    """
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def test_version_parsing(checks: Checks) -> None:
    _banner("VERSION  -  parsing and comparison")
    checks.check("Parses line-1 version comment",
                 hm.parse_hook_version("-- dtc_kneeboard_hook v1.0\n...") == "1.0")
    checks.check("Parses multi-part version",
                 hm.parse_hook_version("-- dtc_kneeboard_hook v2.13\n") == "2.13")
    checks.check("No version comment -> None",
                 hm.parse_hook_version("-- some other file\nlocal x = 1") is None)
    checks.check("Empty / None text -> None",
                 hm.parse_hook_version("") is None and hm.parse_hook_version(None) is None)

    checks.check("1.0 < 1.1 is outdated", hm.is_outdated("1.0", "1.1"))
    checks.check("1.1 vs 1.0 is not outdated", not hm.is_outdated("1.1", "1.0"))
    checks.check("Unversioned existing -> outdated", hm.is_outdated(None, "1.0"))
    checks.check("Unknown bundled -> never outdated", not hm.is_outdated("1.0", None))
    checks.check("Numeric (not lexical) compare: 1.10 > 1.9",
                 hm._version_tuple("1.10") > hm._version_tuple("1.9"))


def test_bundled_template(checks: Checks) -> None:
    _banner("BUNDLED TEMPLATE  -  presence, version and spec constraints")
    text = hm.read_bundled_template()
    if not checks.check("Bundled hook_template.lua is present and readable", bool(text)):
        return

    checks.check("Bundled version is 1.1", hm.bundled_hook_version() == "1.1")
    checks.check("Line 1 is the version comment",
                 text.splitlines()[0].startswith("-- dtc_kneeboard_hook v"))

    required = [
        "DCS.setUserCallbacks",
        "onPlayerChangeSlot",
        "onSimulationFrame",
        "getPlayerUnitType",
        "MiG-29",
        "pcall",
        "lfs.writedir",
        ".tmp",          # atomic write to a temp file
        "os.rename",     # then rename into place
    ]
    missing = [token for token in required if token not in text]
    checks.check("Template contains all required behaviours", not missing,
                 f"missing: {missing}" if missing else "all present")

    # Spec section 2 forbids these in the hooks context. Check the code only:
    # the header comment legitimately names them while documenting their absence.
    code = _strip_lua_comments(text)
    forbidden = ["io.popen", "getMissionLoaded", "lfs.dir(", "Export.lua"]
    present = [token for token in forbidden if token in code]
    checks.check("Template code avoids all forbidden calls", not present,
                 f"found: {present}" if present else "none present")

    checks.check("Initial delay is 600+ frames", "600" in text)
    checks.check("Max retries is 30", "30" in text)


def test_install_and_update(checks: Checks) -> None:
    _banner("INSTALL / UPDATE  -  install, no-op, update, no-downgrade")
    bundled = hm.bundled_hook_version()
    template = hm.read_bundled_template()
    if not template or not bundled:
        checks.check("Bundled template available for install tests", False)
        return

    with tempfile.TemporaryDirectory() as tmp:
        saved_games = Path(tmp) / "Saved Games" / "DCS"  # note: not pre-created
        dest = hm.hook_install_path(saved_games)
        checks.check("hook_install_path = <sg>/Scripts/Hooks/dtc_kneeboard_hook.lua",
                     dest == saved_games / "Scripts" / "Hooks" / "dtc_kneeboard_hook.lua")

        # 1. First install creates the Scripts/Hooks tree and writes the hook.
        result = hm.install_or_update_hook(saved_games)
        checks.check("Missing hook -> INSTALLED",
                     result.action is hm.HookAction.INSTALLED, result.action.value)
        checks.check("Scripts/Hooks directory created", dest.parent.is_dir())
        checks.check("Installed file content matches the bundled template",
                     dest.read_text(encoding="utf-8") == template)

        # 2. Running again is a no-op.
        result = hm.install_or_update_hook(saved_games)
        checks.check("Current hook -> CURRENT (no action)",
                     result.action is hm.HookAction.CURRENT, result.action.value)

        # 3. An older hook is updated; content becomes the bundled template.
        dest.write_text("-- dtc_kneeboard_hook v0.9\n-- stale stub\n", encoding="utf-8")
        result = hm.install_or_update_hook(saved_games)
        checks.check("Older hook -> UPDATED",
                     result.action is hm.HookAction.UPDATED, result.action.value)
        checks.check("Update reports the previous version", result.previous_version == "0.9")
        checks.check("Updated file content matches the bundled template",
                     dest.read_text(encoding="utf-8") == template)

        # 4. An unversioned hook is refreshed.
        dest.write_text("-- no version here\nlocal x = 1\n", encoding="utf-8")
        result = hm.install_or_update_hook(saved_games)
        checks.check("Unversioned hook -> UPDATED",
                     result.action is hm.HookAction.UPDATED, result.action.value)

        # 5. A newer installed hook is never downgraded.
        dest.write_text("-- dtc_kneeboard_hook v9.9\n-- from the future\n", encoding="utf-8")
        result = hm.install_or_update_hook(saved_games)
        checks.check("Newer hook -> CURRENT (no downgrade)",
                     result.action is hm.HookAction.CURRENT, result.action.value)
        checks.check("Newer hook left untouched",
                     dest.read_text(encoding="utf-8").startswith("-- dtc_kneeboard_hook v9.9"))


def test_install_logging(checks: Checks) -> None:
    _banner("INSTALL  -  status messages routed to the callback")
    with tempfile.TemporaryDirectory() as tmp:
        messages: list[str] = []
        hm.install_or_update_hook(Path(tmp) / "DCS", log=messages.append)
        checks.check("Install emits a status message to the callback",
                     any("Installed DCS hook" in m for m in messages),
                     "; ".join(messages))


def main() -> int:
    checks = Checks()
    test_version_parsing(checks)
    test_bundled_template(checks)
    test_install_and_update(checks)
    test_install_logging(checks)
    return 0 if checks.summary() else 1


if __name__ == "__main__":
    sys.exit(main())
