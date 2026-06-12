"""Render kneeboard images from sample DTC data for visual inspection.

Runs the full pipeline (``dtc_processor`` -> ``kneeboard_renderer``) over:

  * the three sample ``.dtc`` files (incl. the attached Contention SARH one),
  * a synthetic "reference replica" exercising every section at once (built to
    mirror the locked design render, so output can be diffed against it),
  * a synthetic "fully empty" case where every section shows NO CONFIG.

All outputs are written as JPEGs to ``App/test_output/`` for manual review.

Usage:
    python render_test_kneeboards.py
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from pathlib import Path

from dtc_processor import load_dtc_file, process_dtc
from kneeboard_renderer import render_kneeboard

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
DTC_DIR = APP_DIR.parent.parent / "DTC Files"
OUTPUT_DIR = APP_DIR / "test_output"

# Three canonical sample files (as referenced by test_dtc_processor.py).
SAMPLE_FILES = [
    "Aerodrome_Point_Test_DTC.dtc",
    "RSBN_Test_DTC.dtc",
    "SPO15 + Points + ADF - Contention SARH Era1.dtc",  # the attached DTC
]

# Fixed timestamp so output is reproducible (matches the design reference).
REFERENCE_TIME = datetime(2026, 5, 8, 14, 32, tzinfo=timezone.utc)


def _slug(name: str) -> str:
    """Turn a DTC filename into a tidy output stem."""
    stem = Path(name).stem
    return "".join(c if c.isalnum() else "_" for c in stem).strip("_").lower()


def render_samples() -> list[Path]:
    """Process and render each real sample DTC file. Returns output paths."""
    outputs: list[Path] = []
    for filename in SAMPLE_FILES:
        path = DTC_DIR / filename
        out = OUTPUT_DIR / f"sample_{_slug(filename)}.jpg"
        try:
            processed = process_dtc(load_dtc_file(path))
        except Exception:  # noqa: BLE001 - keep rendering the other samples
            logger.exception("Failed to process %s", path)
            continue
        render_kneeboard(processed, out, generated=REFERENCE_TIME)
        outputs.append(out)
        logger.info("Rendered %s -> %s", filename, out.name)
    return outputs


def _reference_replica() -> dict:
    """Build a fully-populated kneeboard dict mirroring the design reference.

    Starts from the attached Contention SARH DTC (real Radio/CMDS/SPO-15/ADF
    data) and injects navigation entries plus resolved ADF beacon names so all
    seven sections are populated, matching the locked layout render.
    """
    attached = DTC_DIR / "SPO15 + Points + ADF - Contention SARH Era1.dtc"
    data = process_dtc(load_dtc_file(attached)).to_dict()
    data = copy.deepcopy(data)

    data["waypoints"] = {
        "configured": True,
        "entries": [
            {"num": "WPT1", "name": "Abu al-Duhur", "point_id": "PNT1",
             "display": "WPT1: Abu al-Duhur"},
        ],
    }
    data["airdromes"] = {
        "configured": True,
        "entries": [
            {"num": "ARD1", "name": "Abu al-Duhur", "runway": "—",
             "source_type": "Airdrome", "display": "ARD1: Abu al-Duhur"},
            {"num": "ARD2", "name": "Wujah Al Hajar", "runway": "02",
             "source_type": "Airdrome", "display": "ARD2: Wujah Al Hajar"},
            {"num": "ARD3", "name": "Shayrat", "runway": "—",
             "source_type": "Airdrome", "display": "ARD3: Shayrat"},
        ],
    }
    data["rsbn"] = {
        "configured": True,
        "entries": [
            {"num": "RSBN1", "name": "Krasnodar-Center", "channel": 40,
             "display": "RSBN1: Krasnodar-Center Ch 40"},
        ],
    }
    # Inject resolved beacon names onto the existing ADF channel frequencies.
    adf_names = {
        1: ("Rayak", "Bassel"),
        2: ("Tiyas", "Damascus"),
        3: ("Hama", "Aleppo"),
        4: ("Palmyra", "Beirut"),
    }
    for ch in data["adf"]["channels"]:
        inner_name, outer_name = adf_names.get(ch["channel"], (None, None))
        if ch.get("inner"):
            ch["inner"]["name"] = inner_name
        if ch.get("outer"):
            ch["outer"]["name"] = outer_name
    data["adf"]["configured"] = True
    return data


def _fully_empty() -> dict:
    """Build a kneeboard dict where every section is unconfigured (NO CONFIG)."""
    return {
        "program_name": "Program_1",
        "program_number": 1,
        "profile_name": "",
        "terrain": "Caucasus",
        "aircraft_type": "MiG-29 Fulcrum",
        "waypoints": {"configured": False, "entries": []},
        "airdromes": {"configured": False, "entries": []},
        "rsbn": {"configured": False, "entries": []},
        "adf": {"configured": False, "channels": [
            {"channel": ch, "inner": None, "outer": None} for ch in range(1, 5)
        ]},
        "radio": {"configured": False, "entries": []},
        "cmds": {"configured": False, "params": []},
        "spo15": {"configured": False, "entries": [
            {"position": i + 1, "threat_cyrillic": c, "threat_latin": latin,
             "raw_index": 1, "state": "Off"}
            for i, (c, latin) in enumerate(
                [("П", "P"), ("З", "3"), ("Х", "X"), ("Н", "H"), ("Ф", "F"), ("С", "C")]
            )
        ]},
    }


def render_synthetic() -> list[Path]:
    """Render the synthetic fully-populated and fully-empty cases."""
    outputs = []
    replica_out = OUTPUT_DIR / "synthetic_fully_populated.jpg"
    render_kneeboard(_reference_replica(), replica_out, generated=REFERENCE_TIME)
    outputs.append(replica_out)
    logger.info("Rendered synthetic fully-populated -> %s", replica_out.name)

    empty_out = OUTPUT_DIR / "synthetic_fully_empty.jpg"
    render_kneeboard(_fully_empty(), empty_out, generated=REFERENCE_TIME)
    outputs.append(empty_out)
    logger.info("Rendered synthetic fully-empty -> %s", empty_out.name)
    return outputs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = render_samples() + render_synthetic()
    print(f"\nGenerated {len(outputs)} kneeboard image(s) in {OUTPUT_DIR}:")
    for out in outputs:
        print(f"  - {out.name}")


if __name__ == "__main__":
    main()
