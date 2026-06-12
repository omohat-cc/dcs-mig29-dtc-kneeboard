"""Parse DCS World ``beacons.lua`` files into an ADF frequency lookup.

A DCS ``beacons.lua`` is a Lua *script*, not a plain table literal. It opens with
``dofile``/``require`` calls and ``local`` declarations, then assigns a global
``beacons = { ... }`` array. Each beacon entry also uses two constructs that a
table parser cannot evaluate on its own:

* ``display_name = _('BANIAS')`` - a gettext (i18n) function call.
* ``type = BEACON_TYPE_AIRPORT_HOMER`` - a bare constant defined in an external
  ``BeaconTypes.lua`` that we never load.

Fields are also separated by ``;`` rather than ``,``.

Because of this, ``slpp.decode`` cannot be pointed at the raw file. This module
therefore takes a two-tier approach:

1. **Primary (slpp):** extract the ``beacons = { ... }`` table, strip the gettext
   wrapper, quote the bare ``BEACON_TYPE_*`` constants and normalise the ``;``
   separators, then hand the cleaned table to ``slpp`` for structural parsing.
2. **Fallback (regex):** if slpp is unavailable or fails on any part of the file,
   split the file into per-beacon records and pull out the few fields we need
   (``type``, ``frequency``, ``display_name``, ``callsign``) with regexes.

Both paths produce the same result: a ``{frequency_khz: station_name}`` lookup of
the NDB-class beacons (the "homer" types, which is what the MiG-29 ARK/ADF tunes).

See the technical spec, section 3 ("beacons.lua parsing").
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# slpp is the configured primary parser. Import defensively so the module still
# works through the regex fallback if the dependency is missing. Different slpp
# releases expose either a ready-made ``slpp`` instance or an ``SLPP`` class.
try:  # pragma: no cover - import shape varies by slpp version
    from slpp import slpp as _slpp  # type: ignore
except Exception:  # noqa: BLE001 - any import failure falls back to regex
    try:  # pragma: no cover
        from slpp import SLPP as _SLPP  # type: ignore

        _slpp = _SLPP()
    except Exception:  # noqa: BLE001
        _slpp = None

# In DCS there is no literal "NDB" beacon type. The NDB-equivalent beacons (the
# low/medium-frequency beacons an ADF can home on) are the "homer" types, e.g.
# BEACON_TYPE_AIRPORT_HOMER, BEACON_TYPE_AIRPORT_HOMER_WITH_MARKER,
# BEACON_TYPE_HOMER, BEACON_TYPE_NAUTICAL_HOMER. Matching the substring "HOMER"
# captures them all while excluding VOR/DME/TACAN/VORTAC/RSBN/ILS/broadcast.
NDB_TYPE_KEYWORD = "HOMER"

# Any frequency at or above this many kHz is assumed to have been supplied in Hz
# by mistake and is folded back down to kHz. NDBs top out near 1750 kHz, so this
# threshold (10 MHz) never clips a real NDB but does catch a raw Hz value.
_HZ_DETECT_THRESHOLD_KHZ = 10_000

PathLike = Union[str, Path]


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------
def _read_file(path: PathLike) -> Optional[str]:
    """Read a text file, returning its contents or ``None`` on any I/O error.

    DCS beacon files are UTF-8 (some maps carry non-ASCII station names), so we
    decode as UTF-8 and replace any stray bytes rather than crashing.
    """
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        logger.error("beacons file not found: %s", path)
        return None
    except OSError as exc:
        logger.error("could not read beacons file %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _find_matching_brace(text: str, open_index: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at ``open_index``.

    Brace counting ignores braces that appear inside single- or double-quoted
    Lua strings. Returns ``-1`` if no matching brace is found.
    """
    depth = 0
    in_string: Optional[str] = None
    i = open_index
    n = len(text)
    while i < n:
        c = text[i]
        if in_string is not None:
            if c == "\\":
                i += 2  # skip escaped character
                continue
            if c == in_string:
                in_string = None
        elif c in ("'", '"'):
            in_string = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _extract_beacons_block(text: str) -> Optional[str]:
    """Return the ``{ ... }`` table assigned to the global ``beacons``.

    The returned string includes the outer braces. Returns ``None`` if the
    assignment or its closing brace cannot be located.
    """
    match = re.search(r"\bbeacons\s*=\s*\{", text)
    if not match:
        return None
    open_index = match.end() - 1  # index of the '{'
    close_index = _find_matching_brace(text, open_index)
    if close_index == -1:
        return None
    return text[open_index : close_index + 1]


def _is_ndb(beacon_type: Optional[str]) -> bool:
    """Return ``True`` for NDB-class (homer) beacon types.

    NDBs are the low/medium-frequency beacons the ADF homes on. See
    :data:`NDB_TYPE_KEYWORD`.
    """
    return bool(beacon_type) and NDB_TYPE_KEYWORD in str(beacon_type).upper()


def _normalise_khz(freq: float) -> float:
    """Normalise a frequency to kHz, rounded to one decimal place.

    The DTC supplies ADF frequencies in kHz, but if a caller passes a raw Hz
    value (as stored in beacons.lua) it is folded down to kHz first.
    """
    if freq >= _HZ_DETECT_THRESHOLD_KHZ:
        freq = freq / 1000.0
    return round(freq, 1)


# ---------------------------------------------------------------------------
# Primary parser: slpp
# ---------------------------------------------------------------------------
_GETTEXT_RE = re.compile(r"_\(\s*(['\"])(.*?)\1\s*\)", re.DOTALL)
_BEACON_CONST_RE = re.compile(r"=\s*(BEACON_TYPE_[A-Za-z0-9_]+)")


def _sanitise_for_slpp(block: str) -> str:
    """Turn an extracted ``beacons`` table into something ``slpp`` can decode.

    * ``_('NAME')`` -> ``'NAME'``            (strip the gettext wrapper)
    * ``= BEACON_TYPE_X`` -> ``= 'BEACON_TYPE_X'``  (quote bare constants)
    * ``;`` -> ``,``                          (Lua field separators slpp wants)

    The ``;`` -> ``,`` replacement is safe here because no string value in a DCS
    beacons file contains a semicolon.
    """
    block = _GETTEXT_RE.sub(r"\1\2\1", block)
    block = _BEACON_CONST_RE.sub(r"= '\1'", block)
    block = block.replace(";", ",")
    # The ';' -> ',' swap leaves a comma before each closing brace. The tested
    # slpp release tolerates these, but others reject trailing commas, so strip
    # them (loop to catch nesting) for portability on the Windows target.
    prev = None
    while prev != block:
        prev = block
        block = re.sub(r",(\s*})", r"\1", block)
    return block


def _parse_with_slpp(text: str) -> List[Dict]:
    """Parse the beacons table with slpp. Raises on any failure (caller falls
    back to regex)."""
    if _slpp is None:
        raise RuntimeError("slpp library not available")
    block = _extract_beacons_block(text)
    if block is None:
        raise ValueError("could not locate 'beacons' table in file")

    data = _slpp.decode(_sanitise_for_slpp(block))

    if isinstance(data, dict):
        data = [data]  # single-entry table edge case
    if not isinstance(data, list):
        raise ValueError(f"slpp returned {type(data).__name__}, expected a list")

    beacons: List[Dict] = []
    for entry in data:
        if isinstance(entry, dict):
            beacons.append(
                {
                    "type": entry.get("type"),
                    "frequency": entry.get("frequency"),
                    "display_name": (entry.get("display_name") or ""),
                    "callsign": (entry.get("callsign") or ""),
                }
            )
    if not beacons:
        raise ValueError("slpp produced no beacon entries")
    return beacons


# ---------------------------------------------------------------------------
# Fallback parser: regex
# ---------------------------------------------------------------------------
# Every beacon record contains exactly one display_name field (even ILS
# components, where it is empty). Splitting on display_name therefore yields one
# clean slice per beacon, with all of that beacon's fields after the anchor.
_RECORD_ANCHOR_RE = re.compile(r"\bdisplay_name\s*=")
_FREQ_RE = re.compile(r"\bfrequency\s*=\s*([0-9]+(?:\.[0-9]+)?)")
_TYPE_RE = re.compile(r"\btype\s*=\s*(BEACON_TYPE_[A-Za-z0-9_]+)")
_DISPLAY_RE = re.compile(r"\bdisplay_name\s*=\s*_?\(?\s*(['\"])(.*?)\1", re.DOTALL)
_CALLSIGN_RE = re.compile(r"\bcallsign\s*=\s*(['\"])(.*?)\1", re.DOTALL)


def _split_records(text: str) -> List[str]:
    """Split the file into one substring per beacon, anchored on display_name."""
    starts = [m.start() for m in _RECORD_ANCHOR_RE.finditer(text)]
    return [text[start : (starts[i + 1] if i + 1 < len(starts) else len(text))]
            for i, start in enumerate(starts)]


def _parse_with_regex(text: str) -> List[Dict]:
    """Parse beacons with field regexes. Robust to the gettext/constant syntax
    that defeats a generic Lua table parser."""
    beacons: List[Dict] = []
    for record in _split_records(text):
        type_match = _TYPE_RE.search(record)
        freq_match = _FREQ_RE.search(record)
        if not type_match or not freq_match:
            continue  # not a complete beacon record
        display_match = _DISPLAY_RE.search(record)
        callsign_match = _CALLSIGN_RE.search(record)
        beacons.append(
            {
                "type": type_match.group(1),
                "frequency": float(freq_match.group(1)),
                "display_name": display_match.group(2).strip() if display_match else "",
                "callsign": callsign_match.group(2).strip() if callsign_match else "",
            }
        )
    return beacons


# ---------------------------------------------------------------------------
# Lookup construction
# ---------------------------------------------------------------------------
def _beacons_to_lookup(beacons: List[Dict]) -> Dict[float, str]:
    """Build a ``{freq_khz: station_name}`` lookup from parsed beacon records.

    Only NDB-class beacons are included. The station name is the ``display_name``
    if present, otherwise the ``callsign``, otherwise the frequency itself.
    Frequencies are converted from Hz (as stored in beacons.lua) to kHz.
    """
    lookup: Dict[float, str] = {}
    for beacon in beacons:
        if not _is_ndb(beacon.get("type")):
            continue
        freq_hz = beacon.get("frequency")
        if freq_hz is None:
            continue
        try:
            khz = round(float(freq_hz) / 1000.0, 1)
        except (TypeError, ValueError):
            logger.debug("skipping beacon with non-numeric frequency: %r", freq_hz)
            continue

        name = (beacon.get("display_name") or "").strip()
        if not name:
            name = (beacon.get("callsign") or "").strip()
        if not name:
            name = f"{khz:g} kHz"

        existing = lookup.get(khz)
        if existing is not None and existing != name:
            # Two different NDBs share a frequency. Keep the first and report the
            # collision rather than silently dropping data.
            logger.warning(
                "NDB frequency collision at %g kHz: keeping '%s', ignoring '%s'",
                khz, existing, name,
            )
            continue
        lookup[khz] = name
    return lookup


def parse_beacons_file(path: PathLike) -> Dict[float, str]:
    """Parse a ``beacons.lua`` file into a ``{freq_khz: station_name}`` lookup.

    NDB-class beacons only. Tries slpp first and falls back to regex extraction.
    Returns an empty dict if the file cannot be read or parsed (the ADF section
    then falls back to a raw-frequency display, per the spec).

    Args:
        path: Path to a DCS ``beacons.lua`` file.

    Returns:
        Mapping of NDB frequency in kHz to station name.
    """
    text = _read_file(path)
    if text is None:
        return {}

    beacons: Optional[List[Dict]] = None
    try:
        beacons = _parse_with_slpp(text)
        logger.info("Parsed %s with slpp (%d beacon entries)", path, len(beacons))
    except Exception as exc:  # noqa: BLE001 - any slpp issue triggers the fallback
        logger.warning("slpp parse failed for %s (%s); using regex fallback", path, exc)
        beacons = None

    if not beacons:
        try:
            beacons = _parse_with_regex(text)
            logger.info("Parsed %s with regex fallback (%d beacon entries)", path, len(beacons))
        except Exception as exc:  # noqa: BLE001
            logger.error("regex parse also failed for %s (%s)", path, exc)
            return {}

    lookup = _beacons_to_lookup(beacons)
    logger.info("Built NDB lookup for %s (%d NDB beacons)", path, len(lookup))
    return lookup


def lookup_station(lookup: Dict[float, str], frequency_khz: float) -> Optional[str]:
    """Return the station name for an ADF frequency (kHz), or ``None``.

    Args:
        lookup: A ``{freq_khz: name}`` mapping from :func:`parse_beacons_file`.
        frequency_khz: Frequency to resolve, in kHz (raw Hz is tolerated).

    Returns:
        The station name, or ``None`` if the frequency is not in the lookup.
    """
    if frequency_khz is None:
        return None
    try:
        khz = _normalise_khz(float(frequency_khz))
    except (TypeError, ValueError):
        return None
    return lookup.get(khz)


# ---------------------------------------------------------------------------
# Terrain-aware wrapper with caching
# ---------------------------------------------------------------------------
def build_beacons_path(dcs_install_path: PathLike, terrain: str) -> Path:
    """Construct ``<dcs_install>/Mods/terrains/<terrain>/beacons.lua``.

    Uses :class:`pathlib.Path` so it is correct on both Windows (the target) and
    macOS (development).
    """
    return Path(dcs_install_path) / "Mods" / "terrains" / terrain / "beacons.lua"


class BeaconResolver:
    """Resolve ADF frequencies to station names per terrain.

    Caches the parsed lookup for the most recently used terrain and only
    re-parses when the terrain changes, as required by the spec.
    """

    def __init__(self, dcs_install_path: PathLike) -> None:
        """Args:
            dcs_install_path: Root of the DCS World installation, e.g.
                ``D:\\DCS World``.
        """
        self._dcs_install_path = Path(dcs_install_path)
        self._cached_terrain: Optional[str] = None
        self._cached_lookup: Dict[float, str] = {}

    @property
    def dcs_install_path(self) -> Path:
        """The DCS install root this resolver reads beacons from."""
        return self._dcs_install_path

    def get_lookup(self, terrain: str) -> Dict[float, str]:
        """Return the ``{freq_khz: name}`` lookup for ``terrain``.

        Parses ``beacons.lua`` for the terrain on first use and caches it. A
        repeat call for the same terrain returns the cached result; a different
        terrain triggers a re-parse.
        """
        if terrain == self._cached_terrain:
            return self._cached_lookup
        path = build_beacons_path(self._dcs_install_path, terrain)
        logger.info("Loading beacons for terrain '%s' from %s", terrain, path)
        self._cached_lookup = parse_beacons_file(path)
        self._cached_terrain = terrain
        return self._cached_lookup

    def lookup(self, terrain: str, frequency_khz: float) -> Optional[str]:
        """Resolve an ADF frequency (kHz) on a terrain to a station name."""
        return lookup_station(self.get_lookup(terrain), frequency_khz)


# Module-level convenience wrapper with a single-slot cache, matching the spec's
# "takes the DCS install path and terrain name ... returns the lookup" wrapper.
_DEFAULT_RESOLVER: Optional[BeaconResolver] = None


def get_beacon_lookup(dcs_install_path: PathLike, terrain: str) -> Dict[float, str]:
    """Return the NDB lookup for a terrain, caching across calls.

    Re-parses only when the terrain (or the DCS install path) changes.

    Args:
        dcs_install_path: Root of the DCS World installation.
        terrain: Terrain/map name, e.g. ``"Syria"``.

    Returns:
        Mapping of NDB frequency in kHz to station name (empty if not found).
    """
    global _DEFAULT_RESOLVER
    if _DEFAULT_RESOLVER is None or _DEFAULT_RESOLVER.dcs_install_path != Path(dcs_install_path):
        _DEFAULT_RESOLVER = BeaconResolver(dcs_install_path)
    return _DEFAULT_RESOLVER.get_lookup(terrain)


# ---------------------------------------------------------------------------
# CLI: dump NDBs from a beacons.lua and optionally resolve frequencies
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if len(sys.argv) < 2:
        print("usage: python beacon_parser.py <beacons.lua> [freq_khz ...]")
        raise SystemExit(2)

    table = parse_beacons_file(sys.argv[1])
    print(f"\nFound {len(table)} NDB beacons:")
    for khz in sorted(table):
        print(f"  {khz:>8g} kHz   {table[khz]}")

    for arg in sys.argv[2:]:
        resolved = lookup_station(table, float(arg))
        print(f"lookup {arg} kHz -> {resolved}")
