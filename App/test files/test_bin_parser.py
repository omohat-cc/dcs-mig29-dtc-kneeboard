"""Test / demonstration harness for :mod:`bin_parser`.

Runs the binary DTC extractor against a real DCS session temp directory, prints
the recovered profile name / map / active program / point count, confirms the
extracted structure is what :mod:`dtc_processor` expects (by feeding it straight
in), and exercises the failure paths: no matching files, a corrupted binary, and
a malformed-JSON payload.

Usage:
    python3 test_bin_parser.py

Exits non-zero if any verification check fails, so it can be wired into CI.
"""

from __future__ import annotations

import logging
import struct
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Make the App modules importable regardless of the invocation directory.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from bin_parser import (  # noqa: E402
    extract_dtc_from_directory,
    extract_json_from_bin,
    find_dtc_bin_files,
)
from dtc_processor import process_dtc  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

# The sample temp directory and standalone DTC file are local-only fixtures in
# the utility folder, not distributed with the repo; the checks that need them
# skip cleanly when they are absent.
UTILITY_DIR = APP_DIR.parent
SAMPLE_TEMP_DIR = UTILITY_DIR / "Probe logs" / "Temp File 2"
SAMPLE_DTC_BIN_NAME = "~tr00005738.bin"
ROOT_DTC_BIN = UTILITY_DIR / "~tr00000829.bin"

# Section keys dtc_processor reads out of the active program. Used for the
# structural comparison below.
EXPECTED_PROGRAM_SECTIONS = ["ADF", "Airdromes", "CMDS", "Points", "Radio", "RSBN", "Waypoints"]


class Checks:
    """Collects pass/fail verification results without aborting on the first failure."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        """Record and print a single verification result."""
        if condition:
            self.passed += 1
            status = "PASS"
        else:
            self.failed += 1
            status = "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"    [{status}] {label}{suffix}")

    def summary(self) -> bool:
        """Print a final tally and return True if everything passed."""
        total = self.passed + self.failed
        print("\n" + "=" * 70)
        print(f"VERIFICATION: {self.passed}/{total} checks passed, {self.failed} failed.")
        print("=" * 70)
        return self.failed == 0


def _banner(title: str) -> None:
    print("\n" + "#" * 70)
    print(f"# {title}")
    print("#" * 70)


@contextmanager
def _expect_errors() -> Iterator[None]:
    """Silence bin_parser's logging for tests that intentionally trigger failures.

    The negative tests below deliberately feed in bad data, so the module's
    ERROR/WARNING logs are expected. Suppress them to keep the output readable.
    """
    module_logger = logging.getLogger("bin_parser")
    previous = module_logger.level
    module_logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        module_logger.setLevel(previous)


# ---------------------------------------------------------------------------
# Fixture builders for the failure-path tests
# ---------------------------------------------------------------------------

def _write_marked_run(payload: bytes) -> bytes:
    """Wrap a JSON payload as one length-prefixed record, like DCS does.

    Prepends the 2-byte big-endian length so :func:`bin_parser._clean_run`
    recognises and strips it, mirroring the real container format.
    """
    return struct.pack(">H", len(payload)) + payload


def _make_corrupt_bin(path: Path) -> None:
    """Write a >500 KB file that passes the marker sniff but holds no DTC JSON.

    The marker strings sit in the first 64 KB (so the header check passes) but
    are surrounded by NUL bytes, so they never form a printable run >= 50 chars
    and no JSON object can be recovered.
    """
    blob = bytearray(600 * 1024)
    header = b"\x00" * 64 + b"MiG-29 Fulcrum" + b"\x00" * 64 + b'"data"' + b"\x00" * 64
    blob[: len(header)] = header
    path.write_bytes(blob)


def _make_malformed_json_bin(path: Path) -> None:
    """Write a file whose recovered text starts a ``{"data": ...}`` object but is invalid JSON."""
    # A printable run >= 50 chars containing a syntactically broken object: the
    # "type" key has no value, so json.loads must fail after brace-matching.
    broken = b'{"data": {"name": "deliberately broken DTC payload for testing", "type": }}'
    path.write_bytes(_write_marked_run(broken))


def _make_text_no_data_bin(path: Path) -> None:
    """Write a file with a long printable run but no ``{"data":`` object at all."""
    payload = b"this is plainly not a DTC cartridge file, just filler text. " * 4
    path.write_bytes(_write_marked_run(payload))


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def run_sample_directory(checks: Checks) -> None:
    """Extract from the real sample temp dir and verify the recovered DTC."""
    _banner(f"SAMPLE TEMP DIRECTORY  -  {SAMPLE_TEMP_DIR.name}")

    if not SAMPLE_TEMP_DIR.is_dir():
        print(f"  (skipped: sample temp directory not present: {SAMPLE_TEMP_DIR})")
        return

    # Scan / size filter / sort: the 26 MB track file and 131 KB telemetry file
    # must be excluded, leaving only the ~917 KB DTC file.
    candidates = find_dtc_bin_files(SAMPLE_TEMP_DIR)
    names = [path.name for path in candidates]
    print(f"  Candidates after size filter: {names}")
    checks.check(
        "Size filter leaves exactly one candidate",
        len(candidates) == 1,
        f"{len(candidates)} candidate(s)",
    )
    checks.check(
        f"Candidate is {SAMPLE_DTC_BIN_NAME}",
        bool(candidates) and candidates[0].name == SAMPLE_DTC_BIN_NAME,
        names[0] if names else "(none)",
    )

    # Full extraction pipeline.
    parsed = extract_dtc_from_directory(SAMPLE_TEMP_DIR)
    checks.check("extract_dtc_from_directory returned a dict", isinstance(parsed, dict))
    if not isinstance(parsed, dict):
        return

    data = parsed.get("data", {})
    selected = data.get("SelectedProgram")
    program = data.get(selected, {}) if isinstance(selected, str) else {}
    points = program.get("Points", []) if isinstance(program, dict) else []

    # The four headline values the brief asks for.
    print("\n  Recovered DTC summary:")
    print(f"    Profile name   : {data.get('name')!r}")
    print(f"    Map (terrain)  : {data.get('terrain')!r}")
    print(f"    Active program : {selected!r}")
    print(f"    Point count    : {len(points) if isinstance(points, list) else 0}")

    checks.check("Profile name is non-empty", bool(data.get("name")))
    checks.check("Terrain is 'Syria'", data.get("terrain") == "Syria", str(data.get("terrain")))
    checks.check("Active program is 'Program_1'", selected == "Program_1", str(selected))
    checks.check("Aircraft type is 'MiG-29 Fulcrum'", data.get("type") == "MiG-29 Fulcrum", str(data.get("type")))
    checks.check(
        "Active program has 79 navigation points",
        isinstance(points, list) and len(points) == 79,
        f"{len(points) if isinstance(points, list) else 0} points",
    )

    _verify_processor_compatibility(checks, parsed, program)


def _verify_processor_compatibility(checks: Checks, parsed: dict, program: dict) -> None:
    """Confirm the extracted structure matches what dtc_processor consumes."""
    print("\n  Comparing extracted structure against dtc_processor expectations:")

    # dtc_processor._locate_data needs a top-level "data" object (or a
    # SelectedProgram key); the active program needs the named section keys.
    checks.check("Top-level 'data' object present", isinstance(parsed.get("data"), dict))
    missing = [key for key in EXPECTED_PROGRAM_SECTIONS if key not in program]
    checks.check(
        "Active program has all sections dtc_processor reads",
        not missing,
        f"missing: {missing}" if missing else "ADF/Airdromes/CMDS/Points/Radio/RSBN/Waypoints",
    )
    adf = program.get("ADF", {})
    checks.check(
        "ADF exposes Channel_1_Inner (per-channel beacon keys)",
        isinstance(adf, dict) and "Channel_1_Inner" in adf,
    )

    # The real contract: dtc_processor must resolve the extracted dict cleanly.
    try:
        processed = process_dtc(parsed)
    except Exception as exc:  # noqa: BLE001 - this is the assertion
        checks.check("process_dtc() resolves the extracted DTC", False, f"{type(exc).__name__}: {exc}")
        return

    checks.check("process_dtc() resolves the extracted DTC", True)
    checks.check(
        "Processed profile matches data.name",
        processed.profile_name == parsed["data"].get("name"),
    )
    checks.check(
        "Processed terrain matches data.terrain",
        processed.terrain == parsed["data"].get("terrain"),
    )
    checks.check(
        "Processed program matches SelectedProgram",
        processed.program_name == parsed["data"].get("SelectedProgram"),
    )
    print(
        f"    -> {processed.program_name}: "
        f"{len(processed.waypoints.entries)} waypoint(s), "
        f"{len(processed.airdromes.entries)} airdrome(s), "
        f"{len(processed.radio.entries)} radio channel(s)"
    )


def run_root_sample_file(checks: Checks) -> None:
    """Extract directly from the standalone root DTC .bin and sanity-check it."""
    _banner(f"STANDALONE SAMPLE FILE  -  {ROOT_DTC_BIN.name}")
    if not ROOT_DTC_BIN.is_file():
        print(f"  (skipped: {ROOT_DTC_BIN} not present)")
        return

    parsed = extract_json_from_bin(ROOT_DTC_BIN)
    checks.check("extract_json_from_bin returned a dict", isinstance(parsed, dict))
    if isinstance(parsed, dict):
        data = parsed.get("data", {})
        program = data.get(data.get("SelectedProgram"), {})
        points = program.get("Points", []) if isinstance(program, dict) else []
        print(f"  Profile: {data.get('name')!r}  Points: {len(points)}")
        checks.check("Recovered 79 points from root sample", len(points) == 79, f"{len(points)} points")


def run_edge_cases(checks: Checks) -> None:
    """Exercise the failure paths: no files, corrupt binary, malformed JSON."""
    _banner("EDGE CASES (errors below are expected and suppressed)")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # 1. Empty directory -> no candidates, no extraction.
        with _expect_errors():
            empty_candidates = find_dtc_bin_files(tmp_path)
            empty_result = extract_dtc_from_directory(tmp_path)
        checks.check("Empty directory -> no candidates", empty_candidates == [])
        checks.check("Empty directory -> extract returns None", empty_result is None)

        # 2. Missing directory -> handled, not crashed.
        with _expect_errors():
            missing_result = extract_dtc_from_directory(tmp_path / "does_not_exist")
        checks.check("Missing directory -> extract returns None", missing_result is None)

        # 3. Corrupted binary in the pipeline: passes size + marker checks but
        #    holds no recoverable JSON.
        corrupt = tmp_path / "~tr0000DEAD.bin"
        _make_corrupt_bin(corrupt)
        with _expect_errors():
            corrupt_dir_result = extract_dtc_from_directory(tmp_path)
            corrupt_file_result = extract_json_from_bin(corrupt)
        checks.check(
            "Corrupt binary passes size filter (is a candidate)",
            corrupt in find_dtc_bin_files(tmp_path),
        )
        checks.check("Corrupt binary in pipeline -> returns None", corrupt_dir_result is None)
        checks.check("Corrupt binary direct extract -> returns None", corrupt_file_result is None)

        # 4. Text present but no {"data":} object.
        no_data = tmp_path / "~tr0000BEEF.bin"
        _make_text_no_data_bin(no_data)
        with _expect_errors():
            no_data_result = extract_json_from_bin(no_data)
        checks.check("File with text but no data object -> returns None", no_data_result is None)

        # 5. Malformed JSON payload -> json.loads fails gracefully.
        malformed = tmp_path / "~tr0000F00D.bin"
        _make_malformed_json_bin(malformed)
        with _expect_errors():
            malformed_result = extract_json_from_bin(malformed)
        checks.check("Malformed JSON payload -> returns None", malformed_result is None)

        # 6. Non-existent file path -> returns None, no crash.
        with _expect_errors():
            missing_file_result = extract_json_from_bin(tmp_path / "nope.bin")
        checks.check("Non-existent file -> returns None", missing_file_result is None)


def main() -> int:
    checks = Checks()
    run_sample_directory(checks)
    run_root_sample_file(checks)
    run_edge_cases(checks)
    return 0 if checks.summary() else 1


if __name__ == "__main__":
    sys.exit(main())
