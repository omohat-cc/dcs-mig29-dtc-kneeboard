"""Test / demonstration harness for :mod:`dtc_processor`.

Runs the DTC processor against the three sample ``.dtc`` files, prints the
resolved output for every section, and verifies the expectations from the build
brief.

Usage:
    python3 test_dtc_processor.py

Exits non-zero if any verification check fails, so it can be wired into CI.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Make the App modules importable regardless of the invocation directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dtc_processor import ProcessedDTC, load_dtc_file, process_dtc  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

# Sample DTC files are a local-only fixture in "Mig-29 DTC/DTC Files", outside
# the repo (test files -> App -> DTC Kneeboard Utility -> Mig-29 DTC); the
# checks skip cleanly when the folder is absent.
DTC_DIR = Path(__file__).resolve().parents[3] / "DTC Files"

CONTENTION_FILE = "SPO15 + Points + ADF - Contention SARH E1- Syria.dtc"
AERODROME_FILE = "Aerodrome_Point_Test_DTC.dtc"
RSBN_FILE = "RSBN_Test_DTC.dtc"


class Checks:
    """Collects pass/fail verification results without aborting on the first failure."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0

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

    def skip(self, label: str, reason: str) -> None:
        """Record and print a skipped check (its fixture file is absent)."""
        self.skipped += 1
        print(f"    [SKIP] {label}  ({reason})")

    def summary(self) -> bool:
        """Print a final tally and return True if everything passed."""
        total = self.passed + self.failed
        print("\n" + "=" * 70)
        print(
            f"VERIFICATION: {self.passed}/{total} checks passed, "
            f"{self.failed} failed, {self.skipped} skipped."
        )
        print("=" * 70)
        return self.failed == 0


def _hdr(name: str, configured: bool, detail: str) -> str:
    """Format a section header line, showing NO CONFIG when not configured."""
    state = detail if configured else "NO CONFIG"
    return f"\n  -- {name} [{state}]"


def print_processed(dtc: ProcessedDTC) -> None:
    """Print a human-readable dump of every resolved section."""
    print(f"  Profile : {dtc.profile_name or '(unnamed)'}")
    print(f"  Program : {dtc.program_name} (#{dtc.program_number})")
    print(f"  Terrain : {dtc.terrain}")
    print(f"  Aircraft: {dtc.aircraft_type}")

    print(_hdr("WAYPOINTS", dtc.waypoints.configured, f"{len(dtc.waypoints.entries)} entries"))
    for wp in dtc.waypoints.entries:
        print(f"      {wp.display}   [id={wp.point_id}]")

    print(_hdr("AIRDROMES", dtc.airdromes.configured, f"{len(dtc.airdromes.entries)} entries"))
    for ad in dtc.airdromes.entries:
        print(f"      {ad.display}   RWY {ad.runway}   [{ad.source_type}]")

    print(_hdr("RSBN", dtc.rsbn.configured, f"{len(dtc.rsbn.entries)} entries"))
    for r in dtc.rsbn.entries:
        print(f"      {r.display}")

    beacons = sum((ch.inner is not None) + (ch.outer is not None) for ch in dtc.adf.channels)
    print(_hdr("ADF", dtc.adf.configured, f"{len(dtc.adf.channels)} channels, {beacons} beacons"))
    for ch in dtc.adf.channels:
        inner = ch.inner.display if ch.inner else "(none)"
        outer = ch.outer.display if ch.outer else "(none)"
        print(f"      CH{ch.channel}  Inner: {inner:<14} Outer: {outer}")

    print(_hdr("RADIO", dtc.radio.configured, f"{len(dtc.radio.entries)} channels"))
    for entry in dtc.radio.entries:
        print(f"      {entry.display}")

    print(_hdr("CMDS", dtc.cmds.configured, f"{len(dtc.cmds.params)} params"))
    for param in dtc.cmds.params:
        print(f"      {param.label:<20} = {param.value:<6} [idx {param.raw_index}]")

    print(_hdr("SPO-15 LW", dtc.spo15.configured, f"{len(dtc.spo15.entries)} entries"))
    for entry in dtc.spo15.entries:
        print(f"      {entry.threat_cyrillic} ({entry.threat_latin})  {entry.state:<5} [idx {entry.raw_index}]")


def _banner(title: str, filename: str) -> None:
    print("\n" + "#" * 70)
    print(f"# {title}  -  {filename}")
    print("#" * 70)


def run_contention(checks: Checks) -> None:
    """Process the Contention DTC and verify radio/ADF/CMDS/SPO-15 + empties."""
    _banner("CONTENTION", CONTENTION_FILE)
    contention_path = DTC_DIR / CONTENTION_FILE
    if not contention_path.is_file():
        checks.skip("Contention checks", f"not found: {contention_path}")
        return
    dtc = process_dtc(load_dtc_file(contention_path))
    print_processed(dtc)

    print("\n  Verifying Contention expectations:")
    checks.check(
        "Radio configured with 20 channels",
        dtc.radio.configured and len(dtc.radio.entries) == 20,
        f"{len(dtc.radio.entries)} channels",
    )
    checks.check(
        "ADF configured with 4 channels",
        dtc.adf.configured and len(dtc.adf.channels) == 4,
        f"{len(dtc.adf.channels)} channels",
    )
    beacons = sum((ch.inner is not None) + (ch.outer is not None) for ch in dtc.adf.channels)
    checks.check("ADF resolves 8 beacon entries", beacons == 8, f"{beacons} beacons")
    checks.check(
        "CMDS resolved 6 params",
        dtc.cmds.configured and len(dtc.cmds.params) == 6,
        f"{len(dtc.cmds.params)} params",
    )
    checks.check("SPO-15 resolved 6 entries", len(dtc.spo15.entries) == 6, f"{len(dtc.spo15.entries)} entries")

    # Spot-check CMDS resolution (all source indices are 1 in this file).
    cmds_by_label = {p.label: p.value for p in dtc.cmds.params}
    checks.check("CMDS Salvo Count (SAM) idx 1 -> 6", cmds_by_label.get("Salvo Count (SAM)") == "6", str(cmds_by_label.get("Salvo Count (SAM)")))
    checks.check("CMDS Burst Interval idx 1 -> 0.5s", cmds_by_label.get("Burst Interval") == "0.5s", str(cmds_by_label.get("Burst Interval")))
    checks.check("CMDS Salvo Interval idx 1 -> 5s", cmds_by_label.get("Salvo Interval") == "5s", str(cmds_by_label.get("Salvo Interval")))

    # SPO-15 LW_indices = [3, 2, 1, 2, 3, 1] -> Lock/On/Off/On/Lock/Off.
    states = [e.state for e in dtc.spo15.entries]
    checks.check(
        "SPO-15 states [Lock, On, Off, On, Lock, Off]",
        states == ["Lock", "On", "Off", "On", "Lock", "Off"],
        str(states),
    )

    # Empty array sections must report no-config rather than just an empty list.
    checks.check("Waypoints flagged NO CONFIG", not dtc.waypoints.configured)
    checks.check("Airdromes flagged NO CONFIG", not dtc.airdromes.configured)
    checks.check("RSBN flagged NO CONFIG", not dtc.rsbn.configured)


def run_aerodrome(checks: Checks) -> None:
    """Process the Aerodrome Test DTC and verify waypoint + airdrome resolution."""
    _banner("AERODROME TEST", AERODROME_FILE)
    aerodrome_path = DTC_DIR / AERODROME_FILE
    if not aerodrome_path.is_file():
        checks.skip("Aerodrome checks", f"not found: {aerodrome_path}")
        return
    dtc = process_dtc(load_dtc_file(aerodrome_path))
    print_processed(dtc)

    print("\n  Verifying Aerodrome expectations:")
    checks.check(
        "1 waypoint resolved",
        dtc.waypoints.configured and len(dtc.waypoints.entries) == 1,
        f"{len(dtc.waypoints.entries)} waypoint(s)",
    )
    if dtc.waypoints.entries:
        wp = dtc.waypoints.entries[0]
        checks.check("Waypoint name from Points lookup (AbDuhur)", wp.name == "AbDuhur", wp.name)
        checks.check("Waypoint display 'WPT1: AbDuhur'", wp.display == "WPT1: AbDuhur", wp.display)

    checks.check("2 airdromes resolved", len(dtc.airdromes.entries) == 2, f"{len(dtc.airdromes.entries)} airdrome(s)")
    if len(dtc.airdromes.entries) == 2:
        point_ad, airdrome_ad = dtc.airdromes.entries[0], dtc.airdromes.entries[1]
        checks.check(
            "Airdrome 1 is Point-type, name from Points lookup (AbDuhur)",
            point_ad.source_type == "Point" and point_ad.name == "AbDuhur",
            f"type={point_ad.source_type} name={point_ad.name}",
        )
        checks.check(
            "Airdrome 2 is Airdrome-type, name used directly (Abu al-Duhur)",
            airdrome_ad.source_type == "Airdrome" and airdrome_ad.name == "Abu al-Duhur",
            f"type={airdrome_ad.source_type} name={airdrome_ad.name}",
        )
        checks.check("Airdrome 2 runway side = 09", airdrome_ad.runway == "09", airdrome_ad.runway)

    checks.check("RSBN flagged NO CONFIG", not dtc.rsbn.configured)


def run_rsbn(checks: Checks) -> None:
    """Process the RSBN Test DTC and verify the single RSBN preset."""
    _banner("RSBN TEST", RSBN_FILE)
    rsbn_path = DTC_DIR / RSBN_FILE
    if not rsbn_path.is_file():
        checks.skip("RSBN checks", f"not found: {rsbn_path}")
        return
    dtc = process_dtc(load_dtc_file(rsbn_path))
    print_processed(dtc)

    print("\n  Verifying RSBN expectations:")
    checks.check(
        "1 RSBN entry resolved",
        dtc.rsbn.configured and len(dtc.rsbn.entries) == 1,
        f"{len(dtc.rsbn.entries)} entry/entries",
    )
    if dtc.rsbn.entries:
        rsbn = dtc.rsbn.entries[0]
        checks.check("RSBN name = Krasnodar-Center", rsbn.name == "Krasnodar-Center", rsbn.name)
        checks.check("RSBN channel = 40", rsbn.channel == 40, str(rsbn.channel))
        checks.check(
            "RSBN display 'RSBN1: Krasnodar-Center Ch 40'",
            rsbn.display == "RSBN1: Krasnodar-Center Ch 40",
            rsbn.display,
        )
    checks.check("Waypoints flagged NO CONFIG", not dtc.waypoints.configured)


def main() -> int:
    if not DTC_DIR.is_dir():
        print(f"SKIPPED: DTC samples directory not present: {DTC_DIR}")
        return 0

    checks = Checks()
    run_contention(checks)
    run_aerodrome(checks)
    run_rsbn(checks)
    return 0 if checks.summary() else 1


if __name__ == "__main__":
    sys.exit(main())
