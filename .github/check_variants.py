#!/usr/bin/env python3
"""Guard forked mod variants: they may differ ONLY in their recorded ways.

Some mods ship as deliberate forks (e.g. timer/event wheel-heal variants) whose
behaviour code must stay identical. This script records the allowed difference
per pair as a snapshot, and fails when the live diff deviates from it, i.e.
when someone edits shared behaviour in one fork but not the other.

A snapshot stores only the +/- content lines of a unified diff (no hunk
headers), so shared edits that merely shift line numbers do not break it.

Config: .github/variant-pairs.json - a list of objects:
    {"name": ..., "left": <path>, "right": <path>, "expected": <snapshot path>}
All paths are relative to the repo root.

Usage:
    python3 .github/check_variants.py            # check (what CI runs)
    python3 .github/check_variants.py --update   # re-record current diffs as allowed
"""
import difflib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = HERE / "variant-pairs.json"


def normalised_diff(left: Path, right: Path) -> str:
    """The +/- content lines of a unified diff, without positional noise.

    Drops the two file-header lines positionally rather than by prefix:
    a removed Lua comment also starts with "---", so prefix tests misfire.
    """
    a = left.read_text(encoding="utf-8").splitlines()
    b = right.read_text(encoding="utf-8").splitlines()
    raw = list(difflib.unified_diff(a, b, n=0, lineterm=""))
    body = raw[2:]  # raw is empty for identical files; headers come first otherwise
    lines = [line for line in body if not line.startswith("@@")]
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> int:
    update = "--update" in sys.argv
    if not CONFIG.exists():
        print("No variant-pairs.json - nothing to check")
        return 0

    failed = []
    for pair in json.loads(CONFIG.read_text(encoding="utf-8")):
        name = pair["name"]
        left, right = ROOT / pair["left"], ROOT / pair["right"]
        snapshot = ROOT / pair["expected"]

        missing = [p for p in (left, right) if not p.exists()]
        if missing:
            print(f"FAIL: {name} - missing file(s): {', '.join(str(m) for m in missing)}")
            print("  (if this pair was merged away, remove its entry from variant-pairs.json)")
            failed.append(name)
            continue

        actual = normalised_diff(left, right)

        if update:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(actual, encoding="utf-8")
            print(f"recorded: {name} ({len(actual.splitlines())} allowed diff lines)")
            continue

        expected = snapshot.read_text(encoding="utf-8") if snapshot.exists() else ""
        if actual == expected:
            print(f"ok: {name}")
        else:
            failed.append(name)
            print(f"\nFAIL: {name} - forks have drifted beyond the recorded difference")
            print(f"  left:  {pair['left']}")
            print(f"  right: {pair['right']}")
            delta = difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                "recorded difference", "current difference", lineterm="",
            )
            print("\n".join(delta))

    if update:
        return 0
    if failed:
        print(f"\n{len(failed)} pair(s) out of sync: {', '.join(failed)}")
        print("If the divergence is intentional: python3 .github/check_variants.py --update, then commit the snapshots.")
        return 1
    print("All variant pairs within recorded differences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
