"""Binary temp-file DTC extractor for the DCS MiG-29 DTC Kneeboard Utility.

DCS does not expose the Data Transfer Cartridge (DTC) through any Lua API. The
configuration is instead serialised, as plaintext JSON embedded inside a binary
container, into temp files named ``~trXXXXXXXX.bin`` under
``%LOCALAPPDATA%\\Temp\\DCS\\``. This module locates that file and recovers the
JSON, returning a dict in the same shape as a ``.dtc`` file's content (ready to
hand straight to :mod:`dtc_processor`).

Public entry points
--------------------
* :func:`extract_dtc_from_directory` - full pipeline (scan -> filter -> sniff ->
  extract). This is what the watcher calls.
* :func:`find_dtc_bin_files` - the scan/size-filter/sort step on its own.
* :func:`file_has_dtc_markers` - the cheap 64 KB marker sniff.
* :func:`extract_json_from_bin` - JSON recovery from a single file.

Every public function is defensive: it wraps its work in ``try/except``, logs
the reason on failure, and returns ``None``/``[]`` instead of raising. This runs
inside a long-lived background utility and must never crash it.

Binary format notes (verified against two real session files)
--------------------------------------------------------------
The research findings describe each ~20 KB text record as starting with a
literal ``N `` prefix. That is an over-simplification that breaks on real data:
the two bytes are actually a **2-byte big-endian length** of the payload that
follows (``"N "`` is ``0x4E20`` = 20000, the chunk size). The final, short
record of each JSON copy carries a different length whose low byte is often
non-printable, so it appears either as different printable junk (e.g. ``"G["``
= ``0x475B`` = 18267) or is split off entirely by the run scan. Stripping a
literal ``N `` therefore corrupts roughly one record in six.

The robust rule used here strips a run's first two bytes only when they form a
big-endian integer equal to ``len(run) - 2``. That is self-validating, subsumes
the documented ``N `` case, and leaves already-clean runs untouched. Likewise,
the JSON is pretty-printed, so the object start is matched as ``{`` + optional
whitespace + ``"data":`` rather than the literal ``{"data":``.

One file holds MANY copies of the DTC JSON, appended oldest-first: DCS writes a
fresh serialisation every time the cartridge changes (e.g. via the spawn-selector
DTC manager). The *latest* config is therefore the last copy, so the extractor
returns the newest complete copy, not the first (see :func:`_find_json_object`).

Only the Python standard library is used.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Filename pattern DCS uses for its temp serialisation files.
BIN_GLOB = "~tr*.bin"

# DTC-bearing files were observed at 917 KB-983 KB. The lower bound rejects the
# small telemetry files (~131 KB); the upper bound rejects the large track/
# replay recordings (26 MB+). The build brief specifies 5 MB as the upper bound
# (the technical spec's draft said 2 MB - the brief takes precedence). Both
# bounds are generous around the observed DTC size and trivially adjustable.
MIN_FILE_SIZE = 500 * 1024          # 500 KB
MAX_FILE_SIZE = 5 * 1024 * 1024     # 5 MB

# Bytes sniffed from the head of each candidate to identify the DTC file cheaply
# without reading the whole thing.
HEADER_READ_SIZE = 64 * 1024        # 64 KB

# Printable-ASCII runs shorter than this are noise (binary separators, stray
# bytes) and discarded.
MIN_RUN_LENGTH = 50

# Both marker strings must appear in the header for a file to be the DTC file.
MARKER_AIRCRAFT = b"MiG-29"
MARKER_DATA = b'"data"'

# A maximal run of printable ASCII (32-126) plus tab/newline, length >= the
# minimum. This is the regex form of the research doc's byte-by-byte run scan.
_PRINTABLE_RUN_RE = re.compile(rb"[\x20-\x7e\t\n]{%d,}" % MIN_RUN_LENGTH)

# The DTC JSON is pretty-printed, so the opening brace is separated from the
# "data" key by whitespace. Match that tolerantly.
_JSON_START_RE = re.compile(r'\{\s*"data"\s*:')


# ---------------------------------------------------------------------------
# Directory scan and candidate selection
# ---------------------------------------------------------------------------

def find_dtc_bin_files(
    directory: Union[str, Path],
    min_size: int = MIN_FILE_SIZE,
    max_size: int = MAX_FILE_SIZE,
) -> list[Path]:
    """Return candidate ``~tr*.bin`` files, size-filtered and newest-first.

    Args:
        directory: The DCS temp directory to scan.
        min_size: Inclusive lower size bound in bytes.
        max_size: Inclusive upper size bound in bytes.

    Returns:
        Matching files sorted by modification time, most recent first. Returns
        an empty list if the directory is missing or nothing matches. Never
        raises.
    """
    try:
        dir_path = Path(directory)
        if not dir_path.is_dir():
            logger.error("Temp directory does not exist or is not a directory: %s", dir_path)
            return []

        candidates: list[tuple[float, Path]] = []
        for path in dir_path.glob(BIN_GLOB):
            try:
                stat = path.stat()
            except OSError as exc:
                logger.warning("Could not stat %s: %s", path, exc)
                continue
            if not path.is_file():
                continue
            size = stat.st_size
            if size < min_size or size > max_size:
                logger.debug(
                    "Skipping %s (%d bytes outside [%d, %d]).",
                    path.name, size, min_size, max_size,
                )
                continue
            candidates.append((stat.st_mtime, path))

        candidates.sort(key=lambda item: item[0], reverse=True)
        ordered = [path for _, path in candidates]
        logger.info("Found %d candidate .bin file(s) in %s.", len(ordered), dir_path)
        return ordered
    except Exception as exc:  # never crash the watcher
        logger.exception("Unexpected error scanning %s: %s", directory, exc)
        return []


def file_has_dtc_markers(path: Union[str, Path], header_size: int = HEADER_READ_SIZE) -> bool:
    """Return True if the file's header contains both DTC marker strings.

    Reads only the first ``header_size`` bytes and checks for ``MiG-29`` and
    ``"data"``. Only the DTC-bearing file carries both.

    Args:
        path: The candidate ``.bin`` file.
        header_size: Number of leading bytes to inspect.

    Returns:
        True if both markers are present, else False. Never raises.
    """
    file_path = Path(path)
    try:
        with open(file_path, "rb") as handle:
            header = handle.read(header_size)
    except OSError as exc:
        logger.warning("Could not read header of %s: %s", file_path, exc)
        return False

    has_aircraft = MARKER_AIRCRAFT in header
    has_data = MARKER_DATA in header
    if has_aircraft and has_data:
        return True
    logger.debug(
        "%s missing markers (MiG-29=%s, \"data\"=%s).",
        file_path.name, has_aircraft, has_data,
    )
    return False


# ---------------------------------------------------------------------------
# JSON recovery from the binary container
# ---------------------------------------------------------------------------

def _clean_run(run: bytes) -> bytes:
    """Strip a 2-byte big-endian length marker from a text run if present.

    The marker is removed only when it equals ``len(run) - 2`` (the size of the
    payload that follows it). This is self-validating: a run that already begins
    at its payload - because the marker's low byte was non-printable and split
    off by the run scan - does not match and is returned unchanged.
    """
    if len(run) >= 2:
        marker = (run[0] << 8) | run[1]
        if marker == len(run) - 2:
            return run[2:]
    return run


def _collect_clean_text(data: bytes) -> str:
    """Collect printable-ASCII runs, strip length markers, and concatenate.

    Implements the byte-by-byte run scan from the research doc: each match is a
    maximal run of bytes 32-126 plus tab/newline of length >= MIN_RUN_LENGTH.
    Short runs (the 40-byte binary record separators and stray bytes) fall below
    the threshold and are ignored.
    """
    parts = [_clean_run(match.group()) for match in _PRINTABLE_RUN_RE.finditer(data)]
    return b"".join(parts).decode("ascii", errors="ignore")


def _match_balanced_object(text: str, start: int) -> Optional[str]:
    """Return the brace-balanced object substring beginning at ``start``, or None.

    String-aware brace-depth matching, so braces inside string values (e.g. a
    profile name) are not miscounted. Returns None if the braces never balance,
    which is how a copy truncated mid-write is rejected.
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _find_json_object(text: str) -> Optional[str]:
    """Return the NEWEST complete ``{"data": ...}`` object substring, or None.

    DCS appends a fresh serialisation of the DTC to the temp file each time the
    cartridge changes (e.g. applying a new config in the spawn-selector DTC
    manager), so one file accumulates many copies in chronological order, oldest
    first. We want the *latest* config, so this scans every ``{"data":`` copy and
    returns the last one that is both brace-balanced and valid JSON, falling back
    towards earlier copies if the final one is mid-write/truncated.

    (The previous behaviour returned the *first* copy, so a DTC changed during a
    session was never picked up - confirmed against a real capture whose first
    copies held the old config and whose later copies held the updated one.)
    """
    starts = [m.start() for m in _JSON_START_RE.finditer(text)]
    if not starts:
        logger.error("No '{\"data\": ...}' object found in the extracted text.")
        return None

    for from_end, start in enumerate(reversed(starts)):
        candidate = _match_balanced_object(text, start)
        if candidate is None:
            continue  # truncated/unbalanced copy; try the previous one
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            continue  # balanced but not valid JSON; try the previous one
        copy_number = len(starts) - from_end  # 1-based, counting from file start
        logger.info(
            "Using DTC copy %d of %d (newest valid) from the temp file.",
            copy_number, len(starts),
        )
        return candidate

    logger.error(
        "Found %d '{\"data\":' copy/copies but none were complete, valid JSON.",
        len(starts),
    )
    return None


def extract_json_from_bin(path: Union[str, Path]) -> Optional[dict]:
    """Extract and parse the embedded DTC JSON from one binary temp file.

    Args:
        path: The ``.bin`` file to read.

    Returns:
        The parsed DTC dict (same shape as a ``.dtc`` file's content), or None
        if the file cannot be read, contains no DTC JSON object, or the object
        does not parse. Never raises.
    """
    file_path = Path(path)
    try:
        try:
            data = file_path.read_bytes()
        except OSError as exc:
            logger.error("Could not read binary file %s: %s", file_path, exc)
            return None

        text = _collect_clean_text(data)
        if not text:
            logger.error("No printable text runs in %s; not a DTC file?", file_path.name)
            return None

        json_text = _find_json_object(text)
        if json_text is None:
            return None

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as exc:
            logger.error("DTC JSON in %s failed to parse: %s", file_path.name, exc)
            return None

        if not isinstance(parsed, dict):
            logger.error(
                "Extracted JSON from %s is %s, expected an object.",
                file_path.name, type(parsed).__name__,
            )
            return None

        logger.info("Extracted DTC JSON from %s (%d chars).", file_path.name, len(json_text))
        return parsed
    except Exception as exc:  # never crash the watcher
        logger.exception("Unexpected error extracting DTC from %s: %s", file_path, exc)
        return None


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def extract_dtc_from_directory(
    directory: Union[str, Path],
    min_size: int = MIN_FILE_SIZE,
    max_size: int = MAX_FILE_SIZE,
) -> Optional[dict]:
    """Scan a temp directory and return the parsed DTC dict from the best file.

    Pipeline: glob ``~tr*.bin`` -> size filter -> sort newest-first -> 64 KB
    marker sniff -> JSON extraction. Marker-matching files are tried in order;
    if the newest one fails to yield valid JSON (e.g. it is mid-write or
    corrupt), the next is attempted rather than aborting the whole scan.

    Args:
        directory: The DCS temp directory (e.g. ``%LOCALAPPDATA%\\Temp\\DCS``).
        min_size: Inclusive lower size bound in bytes.
        max_size: Inclusive upper size bound in bytes.

    Returns:
        The parsed DTC dict, or None if no file yields valid DTC data. Never
        raises.
    """
    try:
        candidates = find_dtc_bin_files(directory, min_size, max_size)
        if not candidates:
            logger.warning("No ~tr*.bin candidates in the size range found in %s.", directory)
            return None

        for path in candidates:
            if not file_has_dtc_markers(path):
                continue
            logger.info("Identified candidate DTC file: %s", path.name)
            parsed = extract_json_from_bin(path)
            if parsed is not None:
                return parsed
            logger.warning(
                "Marker-matched file %s did not yield valid DTC JSON; trying next candidate.",
                path.name,
            )

        logger.error("No file in %s yielded valid DTC JSON.", directory)
        return None
    except Exception as exc:  # never crash the watcher
        logger.exception("Unexpected error processing directory %s: %s", directory, exc)
        return None


# ---------------------------------------------------------------------------
# Manual CLI (handy for debugging on the Windows machine)
# ---------------------------------------------------------------------------

def _main() -> int:
    """Extract DTC data from a directory given on the command line and summarise it."""
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    result = extract_dtc_from_directory(target)
    if result is None:
        print(f"No DTC data extracted from {target!r}.")
        return 1

    data = result.get("data", {}) if isinstance(result, dict) else {}
    selected = data.get("SelectedProgram")
    program = data.get(selected, {}) if isinstance(selected, str) else {}
    points = program.get("Points", []) if isinstance(program, dict) else []
    print(f"Profile : {data.get('name') or '(unnamed)'}")
    print(f"Terrain : {data.get('terrain')}")
    print(f"Program : {selected}")
    print(f"Points  : {len(points) if isinstance(points, list) else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
