"""Tests for beacon_parser against the sample DCS beacons.lua files.

Run directly for a human-readable report:

    python3 test_beacon_parser.py
    python3 test_beacon_parser.py /path/to/Syria_beacons.lua /path/to/Caucasus.lua

It will:
  * print every NDB beacon found (name + frequency) for each file,
  * verify the four ADF frequencies from the Contention DTC sample
    (342, 351, 337, 450 kHz) resolve to the expected stations,
  * cross-check the slpp path against the regex fallback (they must agree),
  * exit non-zero if any check fails.

The same checks are also exposed as ``test_*`` functions for pytest.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Dict, Optional

# Make the App modules importable regardless of the invocation directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import beacon_parser as bp  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# beacons.lua fixtures are DCS-owned content and are not distributed with this
# repo. Point DCS_BEACONS_DIR at a folder holding Syria_beacons.lua and
# Caucus_Beacons.lua (or drop/symlink them into test files/fixtures/); when the
# files are absent these checks skip cleanly.
_DEFAULT_DIR = Path(
    os.environ.get("DCS_BEACONS_DIR", str(Path(__file__).resolve().parent / "fixtures"))
)
SYRIA_PATH = _DEFAULT_DIR / "Syria_beacons.lua"
CAUCASUS_PATH = _DEFAULT_DIR / "Caucus_Beacons.lua"

# ADF frequencies from the Contention DTC sample and the station each should
# resolve to (substring match, case-insensitive, to tolerate display formatting).
EXPECTED_SYRIA = {
    342: "DAMASCUS",
    351: "BEIRUT",
    337: "PALMYRA",
    450: "KLEYATE",
}


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------
def print_ndbs(path: Path) -> Dict[float, str]:
    """Parse a beacons file and print every NDB found. Returns the lookup."""
    print(f"\n{'=' * 64}\nNDB beacons in {path.name}\n{'=' * 64}")
    lookup = bp.parse_beacons_file(path)
    if not lookup:
        print("  (none - file missing or unparsable)")
        return lookup
    for khz in sorted(lookup):
        print(f"  {khz:>8g} kHz   {lookup[khz]}")
    print(f"  -> {len(lookup)} NDB beacons")
    return lookup


def _active_parser_name() -> str:
    return "slpp (primary)" if bp._slpp is not None else "regex fallback (slpp not installed)"


# ---------------------------------------------------------------------------
# Checks (return list of failure strings; empty == pass)
# ---------------------------------------------------------------------------
def check_expected_frequencies(lookup: Dict[float, str]) -> list:
    """Verify the four sample ADF frequencies resolve to the expected stations."""
    failures = []
    print(f"\nVerifying Contention DTC ADF frequencies ({_active_parser_name()}):")
    for freq, expected in EXPECTED_SYRIA.items():
        name = bp.lookup_station(lookup, freq)
        ok = name is not None and expected.upper() in name.upper()
        print(f"  {freq:>4} kHz -> {name!r:<24} expect ~{expected!r:<12} {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"{freq} kHz resolved to {name!r}, expected to contain {expected!r}")
    return failures


def check_unknown_returns_none(lookup: Dict[float, str]) -> list:
    """A frequency with no beacon must return None."""
    # 999 kHz is not present in the sample files.
    result = bp.lookup_station(lookup, 999)
    if result is not None:
        return [f"unknown frequency 999 kHz returned {result!r}, expected None"]
    print("\nUnknown frequency 999 kHz -> None  PASS")
    return []


def check_parsers_agree(path: Path) -> list:
    """slpp and regex must build identical lookups (when slpp is available)."""
    text = bp._read_file(path)
    if text is None:
        return [f"could not read {path}"]

    regex_lookup = bp._beacons_to_lookup(bp._parse_with_regex(text))

    if bp._slpp is None:
        print("\nslpp not installed - skipping slpp/regex cross-check.")
        print("  Install with: pip install slpp   (then re-run to validate the primary path)")
        return []

    try:
        slpp_lookup = bp._beacons_to_lookup(bp._parse_with_slpp(text))
    except Exception as exc:  # noqa: BLE001
        return [f"slpp path raised on {path.name}: {exc}"]

    print(f"\nCross-check slpp vs regex on {path.name}:")
    print(f"  slpp:  {len(slpp_lookup)} NDBs")
    print(f"  regex: {len(regex_lookup)} NDBs")
    if slpp_lookup == regex_lookup:
        print("  identical  PASS")
        return []

    only_slpp = {k: v for k, v in slpp_lookup.items() if regex_lookup.get(k) != v}
    only_regex = {k: v for k, v in regex_lookup.items() if slpp_lookup.get(k) != v}
    print(f"  differ  FAIL\n   only/diff in slpp:  {only_slpp}\n   only/diff in regex: {only_regex}")
    return [f"slpp and regex disagree on {path.name}"]


# ---------------------------------------------------------------------------
# pytest entry points
# ---------------------------------------------------------------------------
def test_syria_expected_frequencies() -> None:
    assert SYRIA_PATH.exists(), f"sample file missing: {SYRIA_PATH}"
    assert not check_expected_frequencies(bp.parse_beacons_file(SYRIA_PATH))


def test_syria_unknown_returns_none() -> None:
    assert not check_unknown_returns_none(bp.parse_beacons_file(SYRIA_PATH))


def test_parsers_agree_syria() -> None:
    assert SYRIA_PATH.exists(), f"sample file missing: {SYRIA_PATH}"
    assert not check_parsers_agree(SYRIA_PATH)


# ---------------------------------------------------------------------------
# Script entry point
# ---------------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    syria = Path(argv[0]) if len(argv) >= 1 else SYRIA_PATH
    caucasus = Path(argv[1]) if len(argv) >= 2 else CAUCASUS_PATH

    print(f"slpp available: {bp._slpp is not None}  ->  active parser: {_active_parser_name()}")

    failures: list = []

    # --- Syria: full report + assertions ---
    if syria.exists():
        syria_lookup = print_ndbs(syria)
        failures += check_expected_frequencies(syria_lookup)
        failures += check_unknown_returns_none(syria_lookup)
        failures += check_parsers_agree(syria)
    elif len(argv) >= 1:
        # An explicitly supplied path that does not exist is a real failure.
        failures.append(f"Syria sample not found: {syria}")
    else:
        # The default fixture is local-only (DCS-owned content): skip cleanly.
        print(f"\n(Syria sample not found at {syria} - skipping)")

    # --- Caucasus: report only (no hardcoded expectations) ---
    if caucasus.exists():
        print_ndbs(caucasus)
        failures += check_parsers_agree(caucasus)
    else:
        print(f"\n(Caucasus sample not found at {caucasus} - skipping)")

    print(f"\n{'=' * 64}")
    if failures:
        print(f"RESULT: FAIL ({len(failures)} issue(s))")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS - all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
