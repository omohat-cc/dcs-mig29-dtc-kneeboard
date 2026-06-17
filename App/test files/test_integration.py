"""End-to-end integration tests for the DTC Kneeboard Utility pipeline.

Simulates the full flow the way the running app experiences it (technical spec,
sections 3 and 6):

    mock trigger file -> trigger_watcher -> bin_parser / dtc_processor
                      -> beacon_parser -> kneeboard_renderer -> output JPEG

Covered here:

* Full pipeline driven through :class:`trigger_watcher.TriggerWatcher.poll_once`
  against a real DCS temp ``~tr*.bin`` file, asserting a valid 1536x2048 JPEG
  lands at ``<saved_games>/Kneeboard/MiG-29 Fulcrum/000_dtc_config.jpg``.
* The trigger lifecycle: fresh triggers are processed; stale (>5 min), malformed
  and absent triggers are handled without generating output or crashing.
* ADF beacon-name resolution against a real Syria ``beacons.lua`` (342 kHz ->
  DAMASCUS), plus the raw-frequency fallback when no beacon database is found.
* A direct ``.dtc`` -> process -> render path (no binary container).
* The background watch thread: start, detect a trigger, generate, stop.

This file is self-contained (a small ``Checks`` harness, no pytest needed) and
runnable from anywhere:

    python3 "test files/test_integration.py"

Tests that need fixtures outside the repo (the probe ``.bin`` files, the Syria
``beacons.lua``) skip cleanly if those are absent, so the suite still passes on
a machine that only has the source tree.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- make the App modules importable regardless of where this file lives -----
TEST_DIR = Path(__file__).resolve().parent          # .../App/test files
APP_DIR = TEST_DIR.parent                            # .../App
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

UTILITY_DIR = APP_DIR.parent                         # .../DTC Kneeboard Utility
PROJECT_ROOT = UTILITY_DIR.parent                    # .../Mig-29 DTC

from PIL import Image  # noqa: E402 - after sys.path setup

import config  # noqa: E402
import trigger_watcher  # noqa: E402
from beacon_parser import BeaconResolver  # noqa: E402
from dtc_processor import load_dtc_file, process_dtc  # noqa: E402
from kneeboard_renderer import render_kneeboard  # noqa: E402

# --- fixture locations --------------------------------------------------------
# DTC samples, probe captures and beacons.lua files are local-only fixtures
# (DCS-owned or large material, not distributed with the repo); every check
# that needs an absent fixture skips cleanly. BEACONS_DIR can be overridden
# with the DCS_BEACONS_DIR environment variable.
DTC_FILES_DIR = PROJECT_ROOT / "DTC Files"
PROBE_DIR = UTILITY_DIR / "Probe logs"
BEACONS_DIR = Path(os.environ.get("DCS_BEACONS_DIR", str(TEST_DIR / "fixtures")))

SYRIA_BEACONS = BEACONS_DIR / "Syria_beacons.lua"
REAL_BIN = PROBE_DIR / "Temp File 2" / "~tr00005738.bin"  # Syria DTC, 79 points
ADF_DTC = DTC_FILES_DIR / "SPO15 + Points + ADF - Contention SARH E1- Syria.dtc"
SIMPLE_DTC = DTC_FILES_DIR / "RSBN_Test_DTC.dtc"

CANVAS_SIZE = (1536, 2048)

# Keep the modules quiet; the negative tests intentionally trigger error logs.
logging.basicConfig(level=logging.CRITICAL)


class Checks:
    """Collects pass/fail/skip results without aborting on the first failure."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        status = "PASS" if condition else "FAIL"
        if condition:
            self.passed += 1
        else:
            self.failed += 1
        suffix = f"  ({detail})" if detail else ""
        print(f"    [{status}] {label}{suffix}")
        return condition

    def skip(self, label: str, reason: str) -> None:
        self.skipped += 1
        print(f"    [SKIP] {label}  ({reason})")

    def summary(self) -> bool:
        total = self.passed + self.failed
        print("\n" + "=" * 70)
        print(
            f"INTEGRATION: {self.passed}/{total} checks passed, "
            f"{self.failed} failed, {self.skipped} skipped."
        )
        print("=" * 70)
        return self.failed == 0


def _banner(title: str) -> None:
    print("\n" + "#" * 70)
    print(f"# {title}")
    print("#" * 70)


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------
def _make_sandbox(root: Path, *, with_beacons: bool = False, with_bin: bool = False) -> config.AppConfig:
    """Build a throwaway DCS directory layout and matching config under ``root``.

    Creates ``Saved Games/DCS/Logs`` and a ``temp`` directory. Optionally copies
    a real DTC ``.bin`` into temp and a real Syria ``beacons.lua`` into the fake
    install tree so ADF names resolve.
    """
    saved_games = root / "Saved Games" / "DCS"
    (saved_games / "Logs").mkdir(parents=True, exist_ok=True)
    temp = root / "temp"
    temp.mkdir(exist_ok=True)
    install = root / "DCS World"
    install.mkdir(exist_ok=True)

    if with_beacons and SYRIA_BEACONS.is_file():
        terrain_dir = install / "Mods" / "terrains" / "Syria"
        terrain_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SYRIA_BEACONS, terrain_dir / "beacons.lua")

    if with_bin and REAL_BIN.is_file():
        shutil.copy2(REAL_BIN, temp / REAL_BIN.name)

    return config.AppConfig(
        dcs_install_path=str(install),
        dcs_saved_games_path=str(saved_games),
        dcs_temp_path=str(temp),
    )


# Distinct sentinel for "generate this automatically" (so None can mean "omit").
_AUTO = object()


def _write_trigger(cfg: config.AppConfig, *, age_minutes: float = 0.0,
                   timestamp: object = _AUTO, body: object = None) -> Path:
    """Write a trigger file for ``cfg``; returns its path.

    ``age_minutes`` backdates the auto timestamp. Pass ``timestamp=None`` to omit
    it, or ``body`` (a raw string) to write arbitrary (e.g. malformed) content.
    """
    trigger = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.TRIGGER_SUBPATH)
    trigger.parent.mkdir(parents=True, exist_ok=True)
    if body is not None:
        trigger.write_text(str(body), encoding="utf-8")
        return trigger

    if timestamp is _AUTO:
        when = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        timestamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict = {"aircraft": "MiG-29 Fulcrum", "mission": "test", "theatre": "Syria"}
    if timestamp is not None:
        payload["timestamp"] = timestamp
    trigger.write_text(json.dumps(payload), encoding="utf-8")
    return trigger


def _is_valid_kneeboard(path: Path) -> tuple[bool, str]:
    """Return (ok, detail) after opening ``path`` and checking its dimensions."""
    if not path.is_file():
        return False, "file missing"
    try:
        with Image.open(path) as img:
            size = img.size
            fmt = img.format
    except Exception as exc:  # noqa: BLE001
        return False, f"unreadable: {exc}"
    if size != CANVAS_SIZE:
        return False, f"size {size} != {CANVAS_SIZE}"
    if fmt != "JPEG":
        return False, f"format {fmt} != JPEG"
    return True, f"{size[0]}x{size[1]} {fmt}, {path.stat().st_size} bytes"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_full_pipeline_via_watcher(checks: Checks) -> None:
    """Mock trigger -> watcher.poll_once -> real .bin -> rendered JPEG."""
    _banner("FULL PIPELINE  -  trigger -> bin -> process -> render")
    if not REAL_BIN.is_file():
        checks.skip("Full pipeline via watcher", f"sample .bin not found at {REAL_BIN}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_beacons=True, with_bin=True)
        trigger = _write_trigger(cfg)
        watcher = trigger_watcher.TriggerWatcher(cfg, post_trigger_delay=0.0)

        output = watcher.poll_once()

        checks.check("poll_once() returned an output path", output is not None,
                     str(output))
        expected = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)
        checks.check("Output path matches the spec location",
                     output == expected, str(output))
        if output is not None:
            ok, detail = _is_valid_kneeboard(output)
            checks.check("Generated a valid 1536x2048 JPEG", ok, detail)
        checks.check("Trigger file was consumed (deleted)", not trigger.exists())


def test_trigger_lifecycle(checks: Checks) -> None:
    """Fresh triggers process; stale / malformed / absent ones are ignored."""
    _banner("TRIGGER LIFECYCLE  -  fresh / stale / malformed / absent")

    # --- absent trigger: poll is a harmless no-op ---------------------------
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp))
        watcher = trigger_watcher.TriggerWatcher(cfg, post_trigger_delay=0.0)
        checks.check("No trigger file -> poll_once returns None",
                     watcher.poll_once() is None)

    # --- stale trigger (10 min old): ignored, no output, file removed -------
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_bin=True)
        trigger = _write_trigger(cfg, age_minutes=10.0)
        watcher = trigger_watcher.TriggerWatcher(cfg, post_trigger_delay=0.0)
        output = watcher.poll_once()
        expected = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)
        checks.check("Stale trigger -> poll_once returns None", output is None)
        checks.check("Stale trigger -> no kneeboard written", not expected.exists())
        checks.check("Stale trigger -> file still consumed", not trigger.exists())

    # --- malformed JSON: handled gracefully, file removed -------------------
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_bin=True)
        trigger = _write_trigger(cfg, body="{ this is not valid json ")
        watcher = trigger_watcher.TriggerWatcher(cfg, post_trigger_delay=0.0)
        checks.check("Malformed trigger -> poll_once returns None",
                     watcher.poll_once() is None)
        checks.check("Malformed trigger -> file consumed", not trigger.exists())

    # --- missing timestamp: processed anyway (benefit of the doubt) ---------
    if REAL_BIN.is_file():
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_sandbox(Path(tmp), with_bin=True)
            _write_trigger(cfg, timestamp=None)
            watcher = trigger_watcher.TriggerWatcher(cfg, post_trigger_delay=0.0)
            output = watcher.poll_once()
            checks.check("Trigger without timestamp -> still processed",
                         output is not None and output.is_file(), str(output))
    else:
        checks.skip("Trigger without timestamp -> still processed",
                    "sample .bin not found")


def test_adf_resolution_real_beacons(checks: Checks) -> None:
    """ADF frequencies resolve to station names against a real Syria beacons.lua."""
    _banner("ADF RESOLUTION  -  real Syria beacons.lua")
    if not SYRIA_BEACONS.is_file():
        checks.skip("ADF resolution (real beacons)", f"not found: {SYRIA_BEACONS}")
        return
    if not ADF_DTC.is_file():
        checks.skip("ADF resolution (real beacons)", f"not found: {ADF_DTC}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_beacons=True)
        resolver = BeaconResolver(cfg.dcs_install_path)
        processed = process_dtc(load_dtc_file(ADF_DTC))

        resolved = trigger_watcher.resolve_adf_beacon_names(processed, resolver)
        checks.check("Resolved at least one ADF beacon name", resolved > 0,
                     f"{resolved} resolved")

        names = {
            beacon.name
            for channel in processed.adf.channels
            for beacon in (channel.inner, channel.outer)
            if beacon is not None and beacon.name
        }
        checks.check("342 kHz channel resolved to DAMASCUS", "DAMASCUS" in names,
                     ", ".join(sorted(names)))


def test_adf_fallback_no_beacons(checks: Checks) -> None:
    """With no beacons.lua, ADF stays on raw frequency and the render still works."""
    _banner("ADF FALLBACK  -  no beacons.lua -> raw frequency display")
    if not ADF_DTC.is_file():
        checks.skip("ADF fallback (no beacons)", f"not found: {ADF_DTC}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp))  # install dir has no Mods/terrains/*/beacons.lua
        resolver = BeaconResolver(cfg.dcs_install_path)
        processed = process_dtc(load_dtc_file(ADF_DTC))

        resolved = trigger_watcher.resolve_adf_beacon_names(processed, resolver)
        checks.check("No beacons.lua -> nothing resolved", resolved == 0,
                     f"{resolved} resolved")

        first = processed.adf.channels[0].inner
        checks.check("ADF beacon name left unresolved (raw freq fallback)",
                     first is not None and first.name is None)
        checks.check("resolver=None -> nothing resolved (and no crash)",
                     trigger_watcher.resolve_adf_beacon_names(processed, None) == 0)

        # The renderer must still produce a valid image with unresolved ADF.
        out = Path(tmp) / "fallback.jpg"
        render_kneeboard(processed, out)
        ok, detail = _is_valid_kneeboard(out)
        checks.check("Renders a valid kneeboard despite unresolved ADF", ok, detail)


def test_direct_dtc_pipeline(checks: Checks) -> None:
    """Direct .dtc -> process -> render (no binary container, no watcher)."""
    _banner("DIRECT .DTC PIPELINE  -  process -> render")
    if not SIMPLE_DTC.is_file():
        checks.skip("Direct .dtc pipeline", f"not found: {SIMPLE_DTC}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        processed = process_dtc(load_dtc_file(SIMPLE_DTC))
        out = Path(tmp) / "direct.jpg"
        returned = render_kneeboard(processed, out)
        checks.check("render_kneeboard returned the output path", returned == out)
        ok, detail = _is_valid_kneeboard(out)
        checks.check("Direct .dtc render is a valid 1536x2048 JPEG", ok, detail)


def test_dedupe_unchanged(checks: Checks) -> None:
    """An unchanged DTC is not re-rendered; force=True bypasses the dedupe."""
    _banner("DEDUPE  -  unchanged DTC skips the render (force overrides)")
    if not REAL_BIN.is_file():
        checks.skip("DTC dedupe", f"sample .bin not found at {REAL_BIN}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_bin=True)
        watcher = trigger_watcher.TriggerWatcher(cfg, post_trigger_delay=0.0)
        output = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)
        bin_path = Path(cfg.dcs_temp_path) / REAL_BIN.name

        # Count actual renders by wrapping the renderer the watcher calls.
        real_render = trigger_watcher.render_kneeboard
        render_calls = {"n": 0}

        def counting_render(processed, out):
            render_calls["n"] += 1
            return real_render(processed, out)

        trigger_watcher.render_kneeboard = counting_render
        try:
            # 1. First generation renders.
            first = watcher.generate_kneeboard()
            checks.check("First generate returns a path", first is not None, str(first))
            checks.check("First generate rendered once", render_calls["n"] == 1,
                         f"{render_calls['n']} render(s)")
            ok, detail = _is_valid_kneeboard(output)
            checks.check("First generate wrote a valid JPEG", ok, detail)
            sig_after_first = (output.stat().st_mtime_ns, output.stat().st_size)

            # 2. Nothing changed -> content fingerprint matches: no render, no rewrite.
            second = watcher.generate_kneeboard()
            checks.check("Second generate returns None (skipped)", second is None)
            checks.check("Second generate did not render", render_calls["n"] == 1,
                         f"{render_calls['n']} render(s)")
            checks.check(
                "Second generate did not rewrite the JPEG",
                (output.stat().st_mtime_ns, output.stat().st_size) == sig_after_first,
            )

            # 3. DCS rewrites the temp file with identical content (new mtime, as
            #    seen live in MP): we re-extract, but the content fingerprint
            #    still suppresses the render.
            st = bin_path.stat()
            bumped = st.st_mtime_ns + 5_000_000_000  # +5s, comfortably distinct
            os.utime(bin_path, ns=(bumped, bumped))
            third = watcher.generate_kneeboard()
            checks.check("Third generate (same content, new mtime) returns None",
                         third is None)
            checks.check("Third generate did not render (fingerprint match)",
                         render_calls["n"] == 1, f"{render_calls['n']} render(s)")

            # 4. force=True bypasses the dedupe and re-renders.
            forced = watcher.generate_kneeboard(force=True)
            checks.check("force=True returns a path", forced is not None, str(forced))
            checks.check("force=True rendered again", render_calls["n"] == 2,
                         f"{render_calls['n']} render(s)")
        finally:
            trigger_watcher.render_kneeboard = real_render


def test_on_generated_callback(checks: Checks) -> None:
    """on_generated fires once per actual render, never on a dedupe skip."""
    _banner("ON-GENERATED CALLBACK  -  fires on render, silent on dedupe skip")
    if not REAL_BIN.is_file():
        checks.skip("on_generated callback", f"sample .bin not found at {REAL_BIN}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_bin=True)
        output = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)
        fired: list[Path] = []
        watcher = trigger_watcher.TriggerWatcher(
            cfg, post_trigger_delay=0.0, on_generated=fired.append
        )

        # 1. Actual render -> callback fires once with the output path.
        first = watcher.generate_kneeboard()
        checks.check("First generate returns a path", first is not None, str(first))
        checks.check("Callback fired once on render", len(fired) == 1, f"{len(fired)} call(s)")
        checks.check("Callback received the output path",
                     bool(fired) and fired[0] == output, str(fired[-1] if fired else None))

        # 2. Unchanged DTC -> dedupe skip -> callback must NOT fire.
        second = watcher.generate_kneeboard()
        checks.check("Second generate skipped (None)", second is None)
        checks.check("Callback did not fire on dedupe skip", len(fired) == 1,
                     f"{len(fired)} call(s)")

        # 3. force=True -> renders again -> callback fires again.
        forced = watcher.generate_kneeboard(force=True)
        checks.check("force=True returns a path", forced is not None, str(forced))
        checks.check("Callback fired again on forced render", len(fired) == 2,
                     f"{len(fired)} call(s)")

        # 4. A raising callback must not break the pipeline (still returns a path).
        def boom(_p: Path) -> None:
            raise RuntimeError("callback failure")

        watcher_raise = trigger_watcher.TriggerWatcher(
            cfg, post_trigger_delay=0.0, on_generated=boom
        )
        result = watcher_raise.generate_kneeboard(force=True)
        checks.check("Raising callback does not break generate", result is not None,
                     str(result))


def test_dedupe_via_poll_and_restart(checks: Checks) -> None:
    """In-memory dedupe holds within a session; a fresh watcher re-renders; no sidecar."""
    _banner("DEDUPE  -  poll_once path; in-memory only (restart re-renders)")
    if not REAL_BIN.is_file():
        checks.skip("DTC dedupe (poll/restart)", f"sample .bin not found at {REAL_BIN}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_bin=True)
        output = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)

        watcher = trigger_watcher.TriggerWatcher(cfg, post_trigger_delay=0.0)
        _write_trigger(cfg)
        first = watcher.poll_once()
        checks.check("poll_once first renders", first is not None, str(first))
        sig_after_first = (output.stat().st_mtime_ns, output.stat().st_size)

        _write_trigger(cfg)
        second = watcher.poll_once()
        checks.check("poll_once second (unchanged) skips", second is None)
        checks.check(
            "poll_once second did not rewrite the JPEG",
            (output.stat().st_mtime_ns, output.stat().st_size) == sig_after_first,
        )

        # The dedupe is in-memory only: no fingerprint file is written anywhere.
        checks.check("No fingerprint sidecar in the kneeboard folder",
                     not list(output.parent.glob("*.fingerprint.json")))

        # A brand-new watcher (simulating an app restart) does NOT remember the
        # last render, so the first spawn renders again - it can never get stuck.
        restarted = trigger_watcher.TriggerWatcher(cfg, post_trigger_delay=0.0)
        again = restarted.generate_kneeboard()
        checks.check("Restarted watcher re-renders (no persistence)", again is not None,
                     str(again))


def test_legacy_sidecar_cleanup(checks: Checks) -> None:
    """A stale fingerprint sidecar from an earlier build is deleted on generate."""
    _banner("DEDUPE  -  legacy sidecar cleanup")
    if not REAL_BIN.is_file():
        checks.skip("Legacy sidecar cleanup", f"sample .bin not found at {REAL_BIN}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_bin=True)
        output = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)
        output.parent.mkdir(parents=True, exist_ok=True)
        legacy = output.with_name(output.stem + trigger_watcher.LEGACY_SIDECAR_SUFFIX)
        legacy.write_text('{"fingerprint": "stale"}', encoding="utf-8")

        watcher = trigger_watcher.TriggerWatcher(cfg, post_trigger_delay=0.0)
        result = watcher.generate_kneeboard()
        checks.check("Generated a kneeboard", result is not None, str(result))
        checks.check("Legacy sidecar removed from the kneeboard folder", not legacy.exists())
        checks.check("No .fingerprint.json beside the kneeboard",
                     not list(output.parent.glob("*.fingerprint.json")))


def test_watcher_thread(checks: Checks) -> None:
    """Background thread: start, detect a trigger written after start, stop."""
    _banner("BACKGROUND THREAD  -  start / detect / stop")
    if not REAL_BIN.is_file():
        checks.skip("Background watch thread", f"sample .bin not found at {REAL_BIN}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_beacons=True, with_bin=True)
        watcher = trigger_watcher.TriggerWatcher(
            cfg, poll_interval=0.05, post_trigger_delay=0.0
        )
        expected = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)

        checks.check("Not running before start()", not watcher.is_running())
        watcher.start()
        try:
            checks.check("Running after start()", watcher.is_running())
            trigger = _write_trigger(cfg)

            deadline = time.time() + 5.0
            while time.time() < deadline and not expected.is_file():
                time.sleep(0.05)

            checks.check("Trigger processed by the loop (kneeboard written)",
                         expected.is_file())
            checks.check("Trigger consumed by the loop", not trigger.exists())
        finally:
            watcher.stop()
        checks.check("Not running after stop()", not watcher.is_running())


def test_output_path_spec(checks: Checks) -> None:
    """The trigger and output paths match the exact spec locations."""
    _banner("PATH CONTRACT  -  spec-exact trigger and output locations")
    cfg = config.AppConfig(dcs_saved_games_path="/dcs/sg", dcs_temp_path="/dcs/tmp")
    watcher = trigger_watcher.TriggerWatcher(cfg)
    checks.check(
        "Trigger path = <sg>/Logs/dtc_kneeboard_trigger.json",
        watcher.trigger_path == Path("/dcs/sg/Logs/dtc_kneeboard_trigger.json"),
        str(watcher.trigger_path),
    )
    checks.check(
        "Output path = <sg>/Kneeboard/MiG-29 Fulcrum/000_dtc_config.jpg",
        watcher.output_path == Path("/dcs/sg/Kneeboard/MiG-29 Fulcrum/000_dtc_config.jpg"),
        str(watcher.output_path),
    )


def main() -> int:
    checks = Checks()
    test_full_pipeline_via_watcher(checks)
    test_trigger_lifecycle(checks)
    test_adf_resolution_real_beacons(checks)
    test_adf_fallback_no_beacons(checks)
    test_direct_dtc_pipeline(checks)
    test_dedupe_unchanged(checks)
    test_on_generated_callback(checks)
    test_dedupe_via_poll_and_restart(checks)
    test_legacy_sidecar_cleanup(checks)
    test_watcher_thread(checks)
    test_output_path_spec(checks)
    return 0 if checks.summary() else 1


if __name__ == "__main__":
    sys.exit(main())
