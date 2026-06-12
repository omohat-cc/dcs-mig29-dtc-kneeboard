"""Core DTC processing for the DCS MiG-29 DTC Kneeboard Utility.

This module takes a parsed DTC JSON dictionary (read directly from a ``.dtc``
file during development, or later extracted from a binary ``.bin`` temp file by
``bin_parser``), identifies the active program, and resolves every section into
structured, display-ready data for the kneeboard renderer to consume.

The public entry point is :func:`process_dtc`, which returns a
:class:`ProcessedDTC` dataclass. Call :meth:`ProcessedDTC.to_dict` for a plain
dictionary if a serialisable form is required.

Only the Python standard library is used.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


class DTCProcessingError(Exception):
    """Raised when a parsed DTC dictionary cannot be processed."""


# ---------------------------------------------------------------------------
# Lookup tables and constants (from the technical spec, Section 3)
# ---------------------------------------------------------------------------

# Placeholder shown when a value (e.g. selected runway) is absent. Matches the
# glyph used in the kneeboard visual-design spec for missing data.
NO_VALUE = "—"


def _modulation_label(modulation: Any) -> str:
    """Map a DTC modulation flag to a display label (1 = AM, 0 = FM)."""
    try:
        return "AM" if int(modulation) == 1 else "FM"
    except (TypeError, ValueError):
        logger.debug("Unrecognised modulation value %r; defaulting to AM", modulation)
        return "AM"


# CMDS salvo-count (ZRK/SAM) maps a 1-based index to a hardcoded flare count.
_FLARE_SALVO_ZRK_COUNTS = [6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32]


def _resolve_salvo_zrk(index: int) -> str:
    """Resolve idxFlareSalvoCountZrk via the 1-based hardcoded count array."""
    if 1 <= index <= len(_FLARE_SALVO_ZRK_COUNTS):
        return str(_FLARE_SALVO_ZRK_COUNTS[index - 1])
    logger.warning(
        "idxFlareSalvoCountZrk index %s out of range 1..%d; using raw index",
        index,
        len(_FLARE_SALVO_ZRK_COUNTS),
    )
    return str(index)


@dataclass(frozen=True)
class _CMDSFieldSpec:
    """Display label and index-resolver for one CMDS parameter."""

    label: str
    resolve: Callable[[int], str]


# Ordered mapping of CMDS keys -> (display label, resolver). Insertion order
# drives the kneeboard row order.
CMDS_FIELDS: dict[str, _CMDSFieldSpec] = {
    "idxFlareBurstCountI": _CMDSFieldSpec("Burst Count I", lambda i: str(i)),
    "idxFlareBurstCountII": _CMDSFieldSpec("Burst Count II", lambda i: str(i)),
    "idxFlareBurstInterval": _CMDSFieldSpec("Burst Interval", lambda i: f"{i * 0.5}s"),
    "idxFlareSalvoCountAir": _CMDSFieldSpec("Salvo Count (Air)", lambda i: str(i)),
    "idxFlareSalvoCountZrk": _CMDSFieldSpec("Salvo Count (SAM)", _resolve_salvo_zrk),
    "idxFlareSalvoInterval": _CMDSFieldSpec("Salvo Interval", lambda i: f"{i + 4}s"),
}


# SPO-15 LW threat positions, in order. Each tuple is (Cyrillic glyph, Latin).
SPO15_THREATS: list[tuple[str, str]] = [
    ("П", "P"),
    ("З", "3"),
    ("Х", "X"),
    ("Н", "H"),
    ("Ф", "F"),
    ("С", "C"),
]

# SPO-15 LW index -> state label.
SPO15_STATES: dict[int, str] = {1: "Off", 2: "On", 3: "Lock"}


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WaypointEntry:
    """One resolved navigation waypoint."""

    num: str          # slot label, e.g. "WPT1"
    name: str         # resolved display name
    point_id: str     # source Point id, e.g. "PNT1"
    display: str      # "WPT1: AbDuhur"


@dataclass
class WaypointsSection:
    configured: bool
    entries: list[WaypointEntry]


@dataclass
class AirdromeEntry:
    """One resolved approach airdrome."""

    num: str          # slot label, e.g. "ARD1"
    name: str         # resolved name
    runway: str       # selected runway side, or NO_VALUE
    source_type: str  # "Airdrome" or "Point"
    display: str      # "ARD1: AbDuhur"


@dataclass
class AirdromesSection:
    configured: bool
    entries: list[AirdromeEntry]


@dataclass
class RSBNEntry:
    """One resolved RSBN navigation preset."""

    num: str               # "RSBN1"
    name: str
    channel: Optional[int]
    display: str           # "RSBN1: Krasnodar-Center Ch 40"


@dataclass
class RSBNSection:
    configured: bool
    entries: list[RSBNEntry]


@dataclass
class ADFBeacon:
    """One ADF beacon (inner or outer of a channel)."""

    freq: int             # kHz
    modulation: str       # "AM" / "FM"
    name: Optional[str]   # resolved beacon name (None until beacon lookup added)
    display: str          # "342 kHz AM"


@dataclass
class ADFChannel:
    """One ADF channel with its inner/outer beacon pair."""

    channel: int                  # 1-4
    inner: Optional[ADFBeacon]
    outer: Optional[ADFBeacon]


@dataclass
class ADFSection:
    configured: bool
    channels: list[ADFChannel]


@dataclass
class RadioEntry:
    """One radio channel preset."""

    channel: int       # 0-19
    freq: float        # MHz
    freq_display: str  # "124.000"
    modulation: str    # "AM" / "FM"
    display: str       # "CH 00  124.000  AM"


@dataclass
class RadioSection:
    configured: bool
    entries: list[RadioEntry]


@dataclass
class CMDSParam:
    """One resolved CMDS parameter."""

    key: str          # "idxFlareBurstCountI"
    label: str        # "Burst Count I"
    raw_index: int    # source index value
    value: str        # resolved display value


@dataclass
class CMDSSection:
    configured: bool
    params: list[CMDSParam]


@dataclass
class SPO15Entry:
    """One SPO-15 LW threat-type entry."""

    position: int          # 1-6
    threat_cyrillic: str   # "П"
    threat_latin: str      # "P"
    raw_index: int         # 1-3
    state: str             # "Off" / "On" / "Lock"


@dataclass
class SPO15Section:
    configured: bool
    entries: list[SPO15Entry]


@dataclass
class ProcessedDTC:
    """Fully resolved DTC data for one active program."""

    program_name: str       # "Program_1"
    program_number: int     # 1
    profile_name: str       # data.name
    terrain: str            # "Syria"
    aircraft_type: str      # "MiG-29 Fulcrum"
    waypoints: WaypointsSection
    airdromes: AirdromesSection
    rsbn: RSBNSection
    adf: ADFSection
    radio: RadioSection
    cmds: CMDSSection
    spo15: SPO15Section

    def to_dict(self) -> dict[str, Any]:
        """Return a plain, JSON-serialisable dict of all resolved data."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Section resolvers
# ---------------------------------------------------------------------------

def _build_points_index(program: dict) -> dict[str, dict]:
    """Map each Point's id to its object, for waypoint/airdrome name lookups."""
    index: dict[str, dict] = {}
    points = program.get("Points", [])
    if isinstance(points, list):
        for point in points:
            if isinstance(point, dict) and point.get("id"):
                index[str(point["id"])] = point
    return index


def _point_note(points_index: dict[str, dict], point_id: str) -> str:
    """Return the trimmed note for a Point id, or "" if absent/empty."""
    point = points_index.get(point_id)
    if point:
        return str(point.get("note", "")).strip()
    return ""


def _resolve_waypoints(program: dict, points_index: dict[str, dict]) -> WaypointsSection:
    """Resolve the Waypoints array, naming each via its source Point."""
    entries: list[WaypointEntry] = []
    raw = program.get("Waypoints", [])
    if isinstance(raw, list):
        for wp in raw:
            if not isinstance(wp, dict):
                continue
            point_id = str(wp.get("id", ""))
            num = str(wp.get("num", "")) or point_id
            # Spec: display name = matched Point's note if non-empty, else the id.
            note = _point_note(points_index, point_id)
            name = note if note else point_id
            entries.append(
                WaypointEntry(num=num, name=name, point_id=point_id, display=f"{num}: {name}")
            )
    return WaypointsSection(configured=bool(entries), entries=entries)


def _resolve_airdromes(program: dict, points_index: dict[str, dict]) -> AirdromesSection:
    """Resolve the Airdromes array, branching on the entry ``type`` field."""
    entries: list[AirdromeEntry] = []
    raw = program.get("Airdromes", [])
    if isinstance(raw, list):
        for ad in raw:
            if not isinstance(ad, dict):
                continue
            num = str(ad.get("num", ""))
            source_type = str(ad.get("type", ""))
            if source_type == "Point":
                # Look the name up via the Points array using the entry id.
                point_id = str(ad.get("id", ""))
                note = _point_note(points_index, point_id)
                name = note if note else point_id
            else:
                # "Airdrome" (or unknown): use the name field directly.
                name = str(ad.get("name", "")).strip() or str(ad.get("id", ""))
            runway = str(ad.get("selectedRunwaySide", "")).strip() or NO_VALUE
            entries.append(
                AirdromeEntry(
                    num=num,
                    name=name,
                    runway=runway,
                    source_type=source_type,
                    display=f"{num}: {name}",
                )
            )
    return AirdromesSection(configured=bool(entries), entries=entries)


def _resolve_rsbn(program: dict) -> RSBNSection:
    """Resolve the RSBN array (name + channel)."""
    entries: list[RSBNEntry] = []
    raw = program.get("RSBN", [])
    if isinstance(raw, list):
        for r in raw:
            if not isinstance(r, dict):
                continue
            num = str(r.get("num", ""))
            name = str(r.get("name", "")).strip()
            channel_raw = r.get("channel")
            try:
                channel = int(channel_raw) if channel_raw is not None else None
            except (TypeError, ValueError):
                channel = None
            display = f"{num}: {name} Ch {channel}" if channel is not None else f"{num}: {name}"
            entries.append(RSBNEntry(num=num, name=name, channel=channel, display=display))
    return RSBNSection(configured=bool(entries), entries=entries)


def _build_adf_beacon(entry: Any) -> Optional[ADFBeacon]:
    """Build an ADFBeacon from a raw inner/outer dict (raw freq for now).

    Beacon name resolution against beacons.lua is intentionally deferred; the
    ``name`` field is left as None and the display falls back to raw frequency.
    """
    if not isinstance(entry, dict):
        return None
    try:
        freq = int(entry.get("freq", 0))
    except (TypeError, ValueError):
        freq = 0
    modulation = _modulation_label(entry.get("modulation", 1))
    return ADFBeacon(freq=freq, modulation=modulation, name=None, display=f"{freq} kHz {modulation}")


def _resolve_adf(program: dict) -> ADFSection:
    """Resolve the ADF object into 4 channels, each with an inner/outer pair."""
    channels: list[ADFChannel] = []
    raw = program.get("ADF", {})
    if isinstance(raw, dict):
        for ch in range(1, 5):
            inner = _build_adf_beacon(raw.get(f"Channel_{ch}_Inner"))
            outer = _build_adf_beacon(raw.get(f"Channel_{ch}_Outer"))
            channels.append(ADFChannel(channel=ch, inner=inner, outer=outer))
    configured = any(
        (ch.inner is not None and ch.inner.freq) or (ch.outer is not None and ch.outer.freq)
        for ch in channels
    )
    return ADFSection(configured=configured, channels=channels)


def _resolve_radio(program: dict) -> RadioSection:
    """Resolve the 20 radio channels in numeric order."""
    entries: list[RadioEntry] = []
    raw = program.get("Radio", {})
    if isinstance(raw, dict):
        for ch in range(0, 20):
            cdata = raw.get(f"Channel_{ch}")
            if not isinstance(cdata, dict):
                continue
            try:
                freq = float(cdata.get("freq", 0))
            except (TypeError, ValueError):
                freq = 0.0
            modulation = _modulation_label(cdata.get("modulation", 1))
            freq_display = f"{freq:.3f}"
            display = f"CH {ch:02d}  {freq_display}  {modulation}"
            entries.append(
                RadioEntry(
                    channel=ch,
                    freq=freq,
                    freq_display=freq_display,
                    modulation=modulation,
                    display=display,
                )
            )
    configured = any(e.freq for e in entries)
    return RadioSection(configured=configured, entries=entries)


def _resolve_cmds(program: dict) -> CMDSSection:
    """Resolve the 6 CMDS parameters via the spec lookup tables."""
    params: list[CMDSParam] = []
    raw = program.get("CMDS", {})
    if isinstance(raw, dict):
        for key, spec in CMDS_FIELDS.items():
            if key not in raw:
                logger.debug("CMDS field %s missing from program", key)
                continue
            try:
                index = int(raw[key])
            except (TypeError, ValueError):
                logger.warning("CMDS field %s has non-integer value %r; skipping", key, raw[key])
                continue
            params.append(
                CMDSParam(key=key, label=spec.label, raw_index=index, value=spec.resolve(index))
            )
    return CMDSSection(configured=bool(params), params=params)


def _resolve_spo15(program: dict) -> SPO15Section:
    """Resolve the 6-element LW_indices array (from CMDS) into threat states."""
    entries: list[SPO15Entry] = []
    cmds = program.get("CMDS", {})
    lw = cmds.get("LW_indices") if isinstance(cmds, dict) else None
    if isinstance(lw, list):
        if len(lw) != len(SPO15_THREATS):
            logger.warning(
                "LW_indices has %d entries; expected %d (padding/truncating)",
                len(lw),
                len(SPO15_THREATS),
            )
        for pos, (cyrillic, latin) in enumerate(SPO15_THREATS):
            raw_index = lw[pos] if pos < len(lw) else 1
            try:
                raw_index = int(raw_index)
            except (TypeError, ValueError):
                raw_index = 1
            state = SPO15_STATES.get(raw_index, "Off")
            entries.append(
                SPO15Entry(
                    position=pos + 1,
                    threat_cyrillic=cyrillic,
                    threat_latin=latin,
                    raw_index=raw_index,
                    state=state,
                )
            )
    else:
        logger.debug("No LW_indices array found for SPO-15")
    # "Configured" means at least one threat is actively monitored (On/Lock).
    configured = any(e.state != "Off" for e in entries)
    return SPO15Section(configured=configured, entries=entries)


# ---------------------------------------------------------------------------
# Program selection and public API
# ---------------------------------------------------------------------------

def _program_number(program_name: str) -> int:
    """Extract the trailing integer from a program name (e.g. "Program_1" -> 1)."""
    match = re.search(r"(\d+)\s*$", program_name or "")
    return int(match.group(1)) if match else 0


def _locate_data(parsed: dict) -> dict:
    """Return the inner ``data`` object, accepting wrapped or unwrapped input."""
    if "SelectedProgram" in parsed:
        return parsed
    inner = parsed.get("data")
    if isinstance(inner, dict):
        return inner
    raise DTCProcessingError(
        "Could not locate DTC 'data' object (no 'data' or 'SelectedProgram' key present)."
    )


def _select_program(data: dict) -> tuple[str, dict]:
    """Return (name, object) for the active program, with a safe fallback."""
    selected = data.get("SelectedProgram")
    if isinstance(selected, str) and isinstance(data.get(selected), dict):
        return selected, data[selected]
    logger.warning(
        "SelectedProgram %r missing or invalid; falling back to first available program.",
        selected,
    )
    for key in sorted(k for k in data if isinstance(k, str) and k.startswith("Program_")):
        if isinstance(data[key], dict):
            logger.warning("Using fallback program %s", key)
            return key, data[key]
    raise DTCProcessingError("No valid program (Program_N) found in DTC data.")


def process_dtc(parsed: dict) -> ProcessedDTC:
    """Resolve the active program of a parsed DTC dict into kneeboard data.

    Args:
        parsed: The parsed DTC JSON. May be the full file object (with a
            top-level ``data`` key) or the inner data object directly.

    Returns:
        A :class:`ProcessedDTC` with every section resolved.

    Raises:
        DTCProcessingError: If the data object or active program cannot be found.
    """
    if not isinstance(parsed, dict):
        raise DTCProcessingError(
            f"Expected a dict for the parsed DTC, got {type(parsed).__name__}."
        )

    data = _locate_data(parsed)
    program_name, program = _select_program(data)
    points_index = _build_points_index(program)

    processed = ProcessedDTC(
        program_name=program_name,
        program_number=_program_number(program_name),
        profile_name=str(data.get("name", "")),
        terrain=str(data.get("terrain", "")),
        aircraft_type=str(data.get("type", "")),
        waypoints=_resolve_waypoints(program, points_index),
        airdromes=_resolve_airdromes(program, points_index),
        rsbn=_resolve_rsbn(program),
        adf=_resolve_adf(program),
        radio=_resolve_radio(program),
        cmds=_resolve_cmds(program),
        spo15=_resolve_spo15(program),
    )
    logger.info(
        "Processed %s (terrain=%s): %d waypoint(s), %d airdrome(s), %d RSBN; "
        "ADF=%s Radio=%s CMDS=%s SPO-15=%s",
        program_name,
        processed.terrain,
        len(processed.waypoints.entries),
        len(processed.airdromes.entries),
        len(processed.rsbn.entries),
        "set" if processed.adf.configured else "none",
        "set" if processed.radio.configured else "none",
        "set" if processed.cmds.configured else "none",
        "set" if processed.spo15.configured else "none",
    )
    return processed


def load_dtc_file(path: Union[str, Path]) -> dict:
    """Read and JSON-parse a ``.dtc`` file from disk.

    Args:
        path: Path to the ``.dtc`` file (valid UTF-8 JSON).

    Returns:
        The parsed DTC dictionary.

    Raises:
        DTCProcessingError: If the file cannot be read or parsed.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DTCProcessingError(f"Could not read DTC file '{file_path}': {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DTCProcessingError(f"Could not parse JSON from '{file_path}': {exc}") from exc
