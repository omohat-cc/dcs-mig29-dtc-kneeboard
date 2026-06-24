"""Tests for the Item 5 ground-polling auto-regenerate path in trigger_watcher.

Covers the state-file reader, the on-ground change-check (regenerate only on a
real temp-file change, skip when unchanged, never run airborne/none/stale), the
content-fingerprint dedupe still applying on the ground path, and the
``on_phase_change`` callback firing once per transition.

Self-contained (a small ``Checks`` harness, no pytest). Tests that need the real
DCS ``~tr*.bin`` fixture skip cleanly when it is absent, so this still passes on
a source-only checkout:

    python3 "test files/test_ground_poll.py"
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent          # .../App/test files
APP_DIR = TEST_DIR.parent                            # .../App
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

UTILITY_DIR = APP_DIR.parent                         # .../DTC Kneeboard Utility

import config  # noqa: E402
import trigger_watcher  # noqa: E402

# The real probe capture (Syria DTC) - same fixture the integration test uses.
PROBE_DIR = UTILITY_DIR / "Probe logs"
REAL_BIN = PROBE_DIR / "Temp File 2" / "~tr00005738.bin"

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
            f"GROUND POLL: {self.passed}/{total} checks passed, "
            f"{self.failed} failed, {self.skipped} skipped."
        )
        print("=" * 70)
        return self.failed == 0


def _banner(title: str) -> None:
    print("\n" + "#" * 70)
    print(f"# {title}")
    print("#" * 70)


# ---------------------------------------------------------------------------
# Sandbox + fixture helpers
# ---------------------------------------------------------------------------
def _make_sandbox(root: Path, *, with_bin: bool = False) -> config.AppConfig:
    """Build a throwaway Saved Games + temp layout and matching config."""
    import shutil

    saved_games = root / "Saved Games" / "DCS"
    (saved_games / "Logs").mkdir(parents=True, exist_ok=True)
    temp = root / "temp"
    temp.mkdir(exist_ok=True)
    if with_bin and REAL_BIN.is_file():
        shutil.copy2(REAL_BIN, temp / REAL_BIN.name)
    return config.AppConfig(
        dcs_saved_games_path=str(saved_games),
        dcs_temp_path=str(temp),
    )


def _write_state(cfg: config.AppConfig, phase: str, *, age_seconds: float = 0.0,
                 agl: float = 2.4, omit_ts: bool = False) -> Path:
    """Write a synthetic hook state file for ``cfg``; returns its path."""
    path = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.STATE_SUBPATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"phase": phase, "aircraft": "MiG-29S", "agl_m": agl}
    if not omit_ts:
        when = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        payload["ts"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Counters:
    """Patches extract/render with counting wrappers; restores on exit."""

    def __init__(self) -> None:
        self.extract = 0
        self.render = 0
        self._real_extract = trigger_watcher.extract_dtc_from_directory
        self._real_render = trigger_watcher.render_kneeboard

    def __enter__(self) -> "_Counters":
        def counting_extract(directory, *a, **k):
            self.extract += 1
            return self._real_extract(directory, *a, **k)

        def counting_render(processed, out):
            self.render += 1
            return self._real_render(processed, out)

        trigger_watcher.extract_dtc_from_directory = counting_extract
        trigger_watcher.render_kneeboard = counting_render
        return self

    def __exit__(self, *exc) -> None:
        trigger_watcher.extract_dtc_from_directory = self._real_extract
        trigger_watcher.render_kneeboard = self._real_render


def _watcher(cfg: config.AppConfig, **kwargs):
    """A watcher tuned for tests: ground check every poll, never stale."""
    opts = dict(post_trigger_delay=0.0, ground_poll_seconds=0.0,
                state_stale_seconds=1e9)
    opts.update(kwargs)
    return trigger_watcher.TriggerWatcher(cfg, **opts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_read_state(checks: Checks) -> None:
    """read_state applies the phase + staleness rules; never raises."""
    _banner("READ STATE  -  phase parsing and staleness")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp))
        w = trigger_watcher.TriggerWatcher(cfg, state_stale_seconds=30.0)

        checks.check("Missing state file -> none", w.read_state() == "none")

        _write_state(cfg, "ground")
        checks.check("Fresh ground -> ground", w.read_state() == "ground")

        _write_state(cfg, "air")
        checks.check("Fresh air -> air", w.read_state() == "air")

        _write_state(cfg, "ground", age_seconds=120.0)
        checks.check("Stale ground (120s > 30s) -> none", w.read_state() == "none")

        _write_state(cfg, "weird")
        checks.check("Unknown phase -> none", w.read_state() == "none")

        _write_state(cfg, "ground", omit_ts=True)
        checks.check("Ground with no timestamp -> none", w.read_state() == "none")

        path = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.STATE_SUBPATH)
        path.write_text("{ not valid json", encoding="utf-8")
        checks.check("Corrupt state JSON -> none (no crash)", w.read_state() == "none")


def test_ground_change_and_skip(checks: Checks) -> None:
    """On the ground: a changed .bin regenerates; an unchanged one is skipped."""
    _banner("GROUND CHECK  -  regenerate on change, skip when unchanged")
    if not REAL_BIN.is_file():
        checks.skip("Ground change/skip", f"sample .bin not found at {REAL_BIN}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_bin=True)
        _write_state(cfg, "ground")
        output = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)
        w = _watcher(cfg)

        with _Counters() as c:
            # 1. First on-ground poll sees the .bin as new -> generates.
            first = w.poll_once()
            checks.check("Ground poll regenerates on a new .bin", first is not None,
                         str(first))
            checks.check("Generation parsed the temp file", c.extract == 1,
                         f"{c.extract} extract(s)")
            checks.check("Generation rendered once", c.render == 1, f"{c.render} render(s)")
            checks.check("Kneeboard JPEG written", output.is_file())

            # 2. Nothing changed on disk -> the cheap check skips (no parse/render).
            second = w.poll_once()
            checks.check("Unchanged .bin -> poll returns None", second is None)
            checks.check("Unchanged .bin -> no extra parse", c.extract == 1,
                         f"{c.extract} extract(s)")
            checks.check("Unchanged .bin -> no extra render", c.render == 1,
                         f"{c.render} render(s)")


def test_ground_dedupe_on_changed_mtime(checks: Checks) -> None:
    """force=False on the ground path: changed mtime, same content -> no rewrite."""
    _banner("GROUND DEDUPE  -  changed mtime, identical content, no redraw")
    if not REAL_BIN.is_file():
        checks.skip("Ground dedupe", f"sample .bin not found at {REAL_BIN}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp), with_bin=True)
        _write_state(cfg, "ground")
        output = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)
        bin_path = Path(cfg.dcs_temp_path) / REAL_BIN.name
        w = _watcher(cfg)

        with _Counters() as c:
            first = w.poll_once()
            checks.check("First ground poll renders", first is not None, str(first))
            checks.check("Rendered once", c.render == 1, f"{c.render} render(s)")
            sig = (output.stat().st_mtime_ns, output.stat().st_size)

            # DCS rewrites the temp file with identical content (new mtime).
            st = bin_path.stat()
            bumped = st.st_mtime_ns + 5_000_000_000  # +5s, comfortably distinct
            os.utime(bin_path, ns=(bumped, bumped))

            second = w.poll_once()
            checks.check("Changed mtime -> cheap check fires a re-parse", c.extract == 2,
                         f"{c.extract} extract(s)")
            checks.check("Identical content -> render suppressed (Item 1A dedupe)",
                         c.render == 1, f"{c.render} render(s)")
            checks.check("Ground dedupe -> poll returns None", second is None)
            checks.check("Ground dedupe -> JPEG not rewritten",
                         (output.stat().st_mtime_ns, output.stat().st_size) == sig)


def test_phase_gating(checks: Checks) -> None:
    """air / none / missing / stale phases never run the ground change-check."""
    _banner("PHASE GATING  -  only 'ground' runs the change-check")

    def assert_no_check(label: str, setup) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_sandbox(Path(tmp), with_bin=True)
            output = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.OUTPUT_SUBPATH)
            stale = setup(cfg)  # may return a custom state_stale_seconds
            w = _watcher(cfg, state_stale_seconds=stale or 1e9)
            with _Counters() as c:
                result = w.poll_once()
            checks.check(f"{label}: no generation", result is None and c.extract == 0,
                         f"extract={c.extract}")
            checks.check(f"{label}: no JPEG written", not output.is_file())

    assert_no_check("phase air", lambda cfg: _write_state(cfg, "air") and None)
    assert_no_check("phase none", lambda cfg: _write_state(cfg, "none") and None)
    assert_no_check("state file missing", lambda cfg: None)
    # Stale ground: phase would be ground but the timestamp is old -> treated none.
    assert_no_check("stale ground",
                    lambda cfg: (_write_state(cfg, "ground", age_seconds=120.0), 30.0)[1])


def test_on_phase_change(checks: Checks) -> None:
    """on_phase_change fires exactly once per real transition."""
    _banner("PHASE CALLBACK  -  one fire per transition")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp))  # no .bin: ground checks are harmless no-ops
        fired: list[str] = []
        w = _watcher(cfg, on_phase_change=fired.append)

        for phase in ("ground", "ground", "air", "ground"):  # 2nd ground = no change
            _write_state(cfg, phase)
            w.poll_once()
        path = Path(cfg.dcs_saved_games_path).joinpath(*trigger_watcher.STATE_SUBPATH)
        path.unlink()  # state gone -> none
        w.poll_once()

        checks.check("Fires once per transition (ground, air, ground, none)",
                     fired == ["ground", "air", "ground", "none"], str(fired))


def test_callback_safety(checks: Checks) -> None:
    """A raising on_phase_change must not break poll_once."""
    _banner("PHASE CALLBACK  -  a raising callback is swallowed")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_sandbox(Path(tmp))

        def boom(_phase: str) -> None:
            raise RuntimeError("callback failure")

        w = _watcher(cfg, on_phase_change=boom)
        _write_state(cfg, "ground")
        try:
            w.poll_once()
            ok = True
        except Exception:  # noqa: BLE001
            ok = False
        checks.check("Raising phase callback does not break poll_once", ok)


def main() -> int:
    checks = Checks()
    test_read_state(checks)
    test_ground_change_and_skip(checks)
    test_ground_dedupe_on_changed_mtime(checks)
    test_phase_gating(checks)
    test_on_phase_change(checks)
    test_callback_safety(checks)
    return 0 if checks.summary() else 1


if __name__ == "__main__":
    sys.exit(main())
