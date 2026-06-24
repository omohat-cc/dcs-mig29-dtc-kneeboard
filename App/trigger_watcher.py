"""Trigger-file watcher and DTC processing pipeline orchestrator.

This is the heart of the external watcher/processor. It runs a background thread
that polls for the trigger file the DCS Lua hook writes on a MiG-29 spawn and,
on each fresh trigger, runs the full pipeline (technical spec, sections 3 and 6):

    trigger file detected
        -> read + age-check + delete the trigger
        -> wait for DCS to finish writing its temp files
        -> bin_parser:        scan temp dir, extract the DTC JSON
        -> dtc_processor:     resolve the active program into display data
        -> beacon_parser:     resolve ADF frequencies to station names
        -> kneeboard_renderer: draw the JPEG kneeboard
        -> write <saved_games>/Kneeboard/MiG-29 Fulcrum/000_dtc_config.jpg

Design points:

* The watch loop runs on a daemon thread, started/stopped from the GUI.
* Alongside the trigger, the loop reads the hook's air/ground state file (Item
  5): while parked on the ground it change-checks the DTC temp file on a slow
  cadence and regenerates on a real edit; airborne it pauses. Phase transitions
  surface to the GUI via an optional ``on_phase_change`` callback.
* Every sleep is an interruptible ``Event.wait`` so :meth:`TriggerWatcher.stop`
  returns promptly.
* Status messages go both to the standard logging module and to an optional
  callback (the GUI log panel).
* Nothing here is allowed to crash the thread: the loop body, each poll, and the
  whole pipeline are wrapped, and failures are logged and swallowed.

Only the standard library plus the sibling app modules are imported (no
``customtkinter``/``pystray`` dependency), so this module is testable headless.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from beacon_parser import BeaconResolver
from bin_parser import BIN_GLOB, MAX_FILE_SIZE, MIN_FILE_SIZE, extract_dtc_from_directory
from config import (
    DEFAULT_GROUND_POLL_SECONDS,
    DEFAULT_STATE_STALE_SECONDS,
    AppConfig,
)
from dtc_processor import DTCProcessingError, ProcessedDTC, process_dtc
from kneeboard_renderer import KneeboardRenderError, render_kneeboard

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]
LogCallback = Callable[[str], None]
# Invoked with the output JPEG path after a successful render (see
# generate_kneeboard); used by the GUI to play the "kneeboard generated" sound.
OnGeneratedCallback = Callable[[Path], None]
# Invoked with the derived watch phase ("ground" / "air" / "none") whenever it
# changes; used by the GUI to update its DTC Watch label. Must not raise.
OnPhaseChangeCallback = Callable[[str], None]

# Timing defaults (technical spec, section 3).
DEFAULT_POLL_INTERVAL = 2.0        # seconds between trigger-file polls
DEFAULT_POST_TRIGGER_DELAY = 3.0   # seconds to let DCS finish writing temp files
DEFAULT_MAX_TRIGGER_AGE = 300.0    # seconds; triggers older than this are stale

# Locations relative to the Saved Games directory.
TRIGGER_SUBPATH = ("Logs", "dtc_kneeboard_trigger.json")
# The hook's air/ground state file (Item 5): the hook writes it, the watcher
# reads it to decide whether to poll the temp file while parked.
STATE_SUBPATH = ("Logs", "dtc_kneeboard_state.json")
# Note: "Kneeboard" (no 's'), "MiG-29 Fulcrum" (with space) per spec section 3.
# The "000_" prefix sorts the page first in DCS's kneeboard page order.
OUTPUT_SUBPATH = ("Kneeboard", "MiG-29 Fulcrum", "000_dtc_config.jpg")

# An earlier build wrote a dedupe fingerprint into this file beside the output
# JPEG. The dedupe is now in-memory only, so any such file is stale; it is
# deleted on sight so it never lingers in the player's kneeboard folder.
LEGACY_SIDECAR_SUFFIX = ".fingerprint.json"


def _iso_age_seconds(timestamp: object) -> Optional[float]:
    """Return the age in seconds of an ISO 8601 UTC stamp, or ``None``.

    Accepts the hook's ``...Z`` stamps. Returns ``None`` for a missing or
    unparseable value (the caller then treats the source as not live). Quiet by
    design (no logging), as it is called on the state file every poll.
    """
    if not isinstance(timestamp, str) or not timestamp.strip():
        return None
    text = timestamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


# ---------------------------------------------------------------------------
# ADF beacon-name resolution (standalone so it is unit-testable on its own)
# ---------------------------------------------------------------------------
def resolve_adf_beacon_names(
    processed: ProcessedDTC, resolver: Optional[BeaconResolver]
) -> int:
    """Resolve ADF beacon frequencies to station names against beacons.lua.

    The DTC processor leaves ADF beacon names unresolved (raw frequency only);
    this fills them in using the terrain's beacon database. Each beacon whose
    frequency is found has its ``name`` (and name-based ``display``) set in
    place on ``processed``. Frequencies with no match keep their raw-frequency
    display, which is the spec's fallback behaviour.

    Args:
        processed: The resolved DTC; its ADF section is mutated in place.
        resolver: The terrain-aware beacon resolver, or ``None`` to skip
            resolution entirely (e.g. when no DCS install path is configured).

    Returns:
        The number of beacons resolved to a name. Never raises - any lookup
        failure leaves the raw-frequency display intact.
    """
    if resolver is None:
        return 0
    terrain = processed.terrain
    if not terrain:
        logger.warning("No terrain on the processed DTC; skipping ADF beacon resolution.")
        return 0

    resolved = 0
    try:
        for channel in processed.adf.channels:
            for beacon in (channel.inner, channel.outer):
                if beacon is None or not beacon.freq:
                    continue
                try:
                    name = resolver.lookup(terrain, beacon.freq)
                except Exception as exc:  # noqa: BLE001 - fall back to raw freq
                    logger.warning(
                        "Beacon lookup failed for %s kHz on %s: %s", beacon.freq, terrain, exc
                    )
                    name = None
                if name:
                    beacon.name = name
                    beacon.display = name
                    resolved += 1
    except Exception as exc:  # noqa: BLE001 - resolution is best-effort
        logger.exception("ADF beacon resolution error: %s", exc)
    return resolved


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------
class TriggerWatcher:
    """Polls for the DCS trigger file and runs the kneeboard pipeline.

    Intended to be driven from the GUI: construct with the loaded
    :class:`~config.AppConfig` and an optional log callback, then call
    :meth:`start` to begin watching and :meth:`stop` to shut down. The pipeline
    can also be driven directly (without the file poll) via
    :meth:`generate_kneeboard`, which is what the integration tests exercise.
    """

    def __init__(
        self,
        config: AppConfig,
        log_callback: Optional[LogCallback] = None,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        post_trigger_delay: float = DEFAULT_POST_TRIGGER_DELAY,
        max_trigger_age: float = DEFAULT_MAX_TRIGGER_AGE,
        on_generated: Optional[OnGeneratedCallback] = None,
        on_phase_change: Optional[OnPhaseChangeCallback] = None,
        ground_poll_seconds: Optional[float] = None,
        state_stale_seconds: Optional[float] = None,
    ) -> None:
        """Args:
            config: Loaded application configuration (supplies the DCS paths).
            log_callback: Optional callable receiving status message strings for
                a GUI log panel. Errors are included inline.
            poll_interval: Seconds between trigger-file polls.
            post_trigger_delay: Seconds to wait after detecting a trigger before
                scanning temp files (lets DCS finish writing them).
            max_trigger_age: Triggers with a timestamp older than this (seconds)
                are discarded as stale.
            on_generated: Optional callable invoked with the output JPEG path
                after a successful render (never on a dedupe skip or failure).
                Used by the GUI to play a confirmation sound. It must not raise;
                any exception is caught and logged so the pipeline is unaffected.
            on_phase_change: Optional callable invoked with the derived watch
                phase ("ground" / "air" / "none") whenever it changes. Same
                safety contract as ``on_generated``; the GUI uses it to update
                the DTC Watch label (marshalled onto the Tk loop by the caller).
            ground_poll_seconds: Override for the on-ground temp-file check
                cadence; defaults to the config value (or 5.0).
            state_stale_seconds: Override for how old the hook's state file may
                be before it is treated as "no live DCS"; defaults to the config
                value (or 30.0).
        """
        self._config = config
        self._log_callback = log_callback
        self._poll_interval = poll_interval
        self._post_trigger_delay = post_trigger_delay
        self._max_trigger_age = max_trigger_age
        self._on_generated = on_generated
        self._on_phase_change = on_phase_change
        # Item 5 timing: an explicit override wins, else the config value (which
        # is already clamped on load), else the module default.
        self._ground_poll_seconds = (
            ground_poll_seconds if ground_poll_seconds is not None
            else getattr(config, "ground_poll_seconds", DEFAULT_GROUND_POLL_SECONDS)
        )
        self._state_stale_seconds = (
            state_stale_seconds if state_stale_seconds is not None
            else getattr(config, "state_stale_seconds", DEFAULT_STATE_STALE_SECONDS)
        )

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._resolver = self._build_resolver(config)

        # Dedupe state (see generate_kneeboard): the content fingerprint of the
        # last kneeboard rendered, held in memory only. It is deliberately not
        # persisted - each app launch starts fresh, so the first spawn always
        # renders and the dedupe can never get "stuck" across sessions.
        self._last_fingerprint: Optional[str] = None
        self._legacy_sidecar_cleaned = False

        # Item 5 ground-poll state (all touched only on the watch thread):
        #  * _last_phase     - last derived phase, to detect transitions.
        #  * _last_ground_check - monotonic stamp of the last on-ground bin check.
        #  * _last_bin_stat  - (name, mtime, size) of the newest .bin at the last
        #    generation; the cheap change-check compares against it.
        self._last_phase: str = "none"
        self._last_ground_check: float = 0.0
        self._last_bin_stat: Optional[tuple[str, float, int]] = None

    # --- construction helpers ---------------------------------------------
    @staticmethod
    def _build_resolver(config: AppConfig) -> Optional[BeaconResolver]:
        """Build the beacon resolver if a DCS install path is configured."""
        install = getattr(config, "dcs_install_path", None)
        if not install:
            logger.info("No DCS install path configured; ADF names fall back to raw frequency.")
            return None
        try:
            return BeaconResolver(install)
        except Exception as exc:  # noqa: BLE001 - resolver is optional
            logger.warning("Could not create beacon resolver for %s: %s", install, exc)
            return None

    # --- configured paths --------------------------------------------------
    @property
    def saved_games_path(self) -> Optional[Path]:
        """The configured DCS Saved Games directory, if any."""
        value = self._config.dcs_saved_games_path
        return Path(value) if value else None

    @property
    def temp_path(self) -> Optional[Path]:
        """The configured DCS temp directory, if any."""
        value = self._config.dcs_temp_path
        return Path(value) if value else None

    @property
    def trigger_path(self) -> Optional[Path]:
        """Full path to the trigger file, if the Saved Games path is set."""
        base = self.saved_games_path
        return base.joinpath(*TRIGGER_SUBPATH) if base else None

    @property
    def output_path(self) -> Optional[Path]:
        """Full path to the output kneeboard JPEG, if the Saved Games path is set."""
        base = self.saved_games_path
        return base.joinpath(*OUTPUT_SUBPATH) if base else None

    @property
    def state_path(self) -> Optional[Path]:
        """Full path to the hook's state file, if the Saved Games path is set."""
        base = self.saved_games_path
        return base.joinpath(*STATE_SUBPATH) if base else None

    # --- logging -----------------------------------------------------------
    def _log(self, message: str, level: int = logging.INFO) -> None:
        """Send a message to the logger and the optional GUI callback."""
        logger.log(level, message)
        if self._log_callback is not None:
            try:
                self._log_callback(message)
            except Exception:  # noqa: BLE001 - a GUI callback must never break us
                logger.exception("log callback raised")

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Start the background watch loop (no-op if already running)."""
        if self.is_running():
            self._log("Watcher already running.")
            return
        if self.trigger_path is None:
            self._log("Cannot start watcher: DCS Saved Games path is not configured.", logging.ERROR)
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="dtc-trigger-watcher", daemon=True
        )
        self._thread.start()
        self._log(f"Watching for MiG-29 DTC trigger at {self.trigger_path}")

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the watch loop to stop and wait briefly for it to finish."""
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self._log("Watcher stopped.")

    def is_running(self) -> bool:
        """True if the background watch thread is alive."""
        return bool(self._thread and self._thread.is_alive())

    # --- watch loop --------------------------------------------------------
    def _run_loop(self) -> None:
        """The background loop: poll, then sleep, until stopped. Never crashes."""
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - the loop must never die
                self._log(f"Unexpected watcher error: {exc}", logging.ERROR)
                logger.exception("poll_once crashed")
            # Interruptible sleep so stop() returns promptly.
            self._stop_event.wait(self._poll_interval)

    def poll_once(self) -> Optional[Path]:
        """Run one watch poll: update the phase, handle a trigger, ground-check.

        Order each poll:
          1. Read the hook's state file and emit any phase transition (label +
             log). This is cheap and runs every poll so the label stays live.
          2. The unchanged trigger path: a fresh spawn trigger renders once.
          3. While the derived phase is "ground", an independent (cadenced)
             change-check regenerates if the DTC temp file changed.

        Returns:
            The rendered kneeboard path if one was generated this poll (trigger-
            driven OR ground-driven), else ``None``.
        """
        phase = self._update_phase()

        triggered = self._poll_trigger()
        if triggered is not None:
            # A spawn render happened: sync the ground baseline to the file we
            # just rendered so the ground check does not immediately re-fire for
            # the same write (gotcha: keep the two paths in step).
            self._last_bin_stat = self._current_bin_stat()
            return triggered

        if phase == "ground":
            return self._ground_check()
        return None

    def _poll_trigger(self) -> Optional[Path]:
        """The trigger path (unchanged): consume a fresh trigger and regenerate.

        Returns the rendered path if a trigger was acted on and produced an
        image, else ``None`` (no trigger, a stale/invalid trigger, a dedupe skip
        or a pipeline error).
        """
        trigger = self.trigger_path
        if trigger is None or not trigger.exists():
            return None

        trigger_data = self._consume_trigger(trigger)
        if trigger_data is None:
            return None  # stale or invalid; already logged and the file removed

        self._log("Trigger detected.")
        # Wait for DCS to finish writing temp files (interruptible).
        if self._stop_event.wait(self._post_trigger_delay):
            self._log("Watcher stopping; abandoning this trigger.")
            return None
        return self.generate_kneeboard(trigger_data)

    # --- air/ground state (Item 5) -----------------------------------------
    def read_state(self) -> str:
        """Return the live air/ground phase the hook published.

        Reads ``<Saved Games>/Logs/dtc_kneeboard_state.json`` and returns one of
        ``"ground"``, ``"air"`` or ``"none"``. A missing file, an unreadable or
        invalid file, an unrecognised phase, or a timestamp older than
        ``state_stale_seconds`` (or absent) all yield ``"none"`` - i.e. no live
        DCS, so the app shows Waiting and stays trigger-only, exactly like an
        older hook that never wrote this file. Never raises.
        """
        path = self.state_path
        if path is None or not path.exists():
            return "none"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return "none"
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Could not read state file %s: %s", path, exc)
            return "none"

        phase = data.get("phase")
        if phase not in ("ground", "air"):
            return "none"
        # A missing or stale timestamp means the hook/DCS is no longer live.
        age = _iso_age_seconds(data.get("ts"))
        if age is None or age > self._state_stale_seconds:
            return "none"
        return phase

    def _update_phase(self) -> str:
        """Read the phase, log + notify on a transition, and return it."""
        phase = self.read_state()
        if phase == self._last_phase:
            return phase

        previous = self._last_phase
        self._last_phase = phase
        if phase == "ground":
            # Check the temp file promptly on (re)entering the ground phase.
            self._last_ground_check = 0.0
            if previous == "air":
                self._log("Back on the ground - DTC watch resumed.")
            else:
                self._log(
                    "MiG-29 on the ground - watching DTC for changes "
                    f"(every {int(self._ground_poll_seconds)}s)."
                )
        elif phase == "air":
            self._log("Airborne - DTC watch paused.")
        else:  # none
            self._log("No live DCS state - watching for the next spawn trigger only.")
        self._notify_phase_change(phase)
        return phase

    def _notify_phase_change(self, phase: str) -> None:
        """Invoke the phase-change callback, if set. Never raises into the loop."""
        if self._on_phase_change is None:
            return
        try:
            self._on_phase_change(phase)
        except Exception:  # noqa: BLE001 - a callback must never break the loop
            logger.exception("on_phase_change callback raised")

    def _current_bin_stat(self) -> Optional[tuple[str, float, int]]:
        """Cheaply identify the newest candidate ``~tr*.bin`` (no content read).

        Scans the temp dir for size-banded ``~tr*.bin`` files and returns
        ``(name, mtime, size)`` of the newest by mtime, or ``None`` if the temp
        path is unset/missing or nothing qualifies. Uses :func:`os.scandir`, so
        the size/mtime come from the directory entry without an extra read - safe
        to call every few seconds. Never raises.
        """
        temp = self.temp_path
        if temp is None:
            return None
        try:
            newest: Optional[tuple[str, float, int]] = None
            with os.scandir(temp) as entries:
                for entry in entries:
                    if not fnmatch.fnmatch(entry.name, BIN_GLOB):
                        continue
                    try:
                        if not entry.is_file():
                            continue
                        stat = entry.stat()
                    except OSError:
                        continue
                    if stat.st_size < MIN_FILE_SIZE or stat.st_size > MAX_FILE_SIZE:
                        continue
                    if newest is None or stat.st_mtime > newest[1]:
                        newest = (entry.name, stat.st_mtime, stat.st_size)
            return newest
        except OSError as exc:
            logger.debug("Could not scan temp dir %s for the ground check: %s", temp, exc)
            return None

    def _ground_check(self) -> Optional[Path]:
        """While parked, regenerate if the newest DTC temp file changed.

        Runs at most every ``ground_poll_seconds``. The cheap ``(name, mtime,
        size)`` signature means an unchanged temp dir does no parse or render. On
        a change it calls ``generate_kneeboard(force=False)`` so the content
        fingerprint dedupe still suppresses a needless redraw when a re-applied
        DTC is byte-for-byte identical (only a genuine edit redraws). The
        baseline is updated after the check so the same write never fires twice.
        Never raises (the caller wraps the loop, but this stays self-contained).
        """
        now = time.monotonic()
        if now - self._last_ground_check < self._ground_poll_seconds:
            return None
        self._last_ground_check = now

        current = self._current_bin_stat()
        if current is None or current == self._last_bin_stat:
            return None  # nothing new on disk: no parse, no render

        self._last_bin_stat = current
        self._log("DTC change detected on the ground - regenerating kneeboard.")
        return self.generate_kneeboard(force=False)

    # --- trigger handling --------------------------------------------------
    def _consume_trigger(self, trigger: Path) -> Optional[dict]:
        """Read, age-check and delete the trigger file.

        Returns the parsed trigger dict if it is fresh enough to act on, else
        ``None``. The file is always deleted once read (per the spec ordering:
        read, then delete, then process) so it is never reprocessed.
        """
        try:
            text = trigger.read_text(encoding="utf-8")
        except OSError as exc:
            self._log(f"Could not read trigger file: {exc}", logging.ERROR)
            self._delete_trigger(trigger)
            return None

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("trigger JSON is not an object")
        except (json.JSONDecodeError, ValueError) as exc:
            self._log(f"Trigger file is not valid JSON ({exc}); discarding.", logging.WARNING)
            self._delete_trigger(trigger)
            return None

        age = self._trigger_age_seconds(data.get("timestamp"))
        # Delete before processing so a slow pipeline never reprocesses it.
        self._delete_trigger(trigger)

        if age is not None and age > self._max_trigger_age:
            self._log(
                f"Ignoring stale trigger ({int(age)}s old, limit {int(self._max_trigger_age)}s).",
                logging.WARNING,
            )
            return None
        return data

    def _trigger_age_seconds(self, timestamp: object) -> Optional[float]:
        """Return the trigger's age in seconds, or ``None`` if not determinable.

        Accepts the hook's ISO 8601 UTC stamp (``...Z``). A missing or
        unparseable timestamp returns ``None`` (the caller then processes the
        trigger anyway, since it carries our own filename).
        """
        if not isinstance(timestamp, str) or not timestamp.strip():
            self._log("Trigger has no timestamp; processing anyway.", logging.WARNING)
            return None
        text = timestamp.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            self._log(f"Unparseable trigger timestamp {timestamp!r}; processing anyway.", logging.WARNING)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds()

    def _delete_trigger(self, trigger: Path) -> None:
        """Delete the trigger file, logging (not raising) on failure."""
        try:
            trigger.unlink(missing_ok=True)
        except OSError as exc:
            self._log(f"Could not delete trigger file {trigger}: {exc}", logging.WARNING)

    # --- pipeline ----------------------------------------------------------
    def generate_kneeboard(
        self, trigger_data: Optional[dict] = None, *, force: bool = False
    ) -> Optional[Path]:
        """Run the full extract -> process -> resolve -> render pipeline once.

        After extracting and resolving the DTC, its fully resolved form is hashed
        and compared to the last kneeboard rendered (this session). If the hash
        matches, the render is skipped - this avoids needless re-rendering, which
        is wasteful and, on lower-spec PCs, can briefly stutter DCS, and in
        multiplayer the hook can re-fire the trigger repeatedly for one DTC.
        ``ProcessedDTC.to_dict()`` carries no render timestamp, so an identical
        DTC hashes identically while a genuine edit changes the hash and always
        regenerates. The fingerprint is in-memory only (not persisted), so the
        first spawn after each launch always renders.

        Args:
            trigger_data: The parsed trigger (informational only; logged). The
                terrain and aircraft come from the DTC itself, not the trigger.
            force: If True, bypass the dedupe and always re-render. The manual
                "regenerate" action (a later item) uses this.

        Returns:
            The kneeboard JPEG path **only if a fresh image was rendered this
            call**; otherwise ``None`` - whether there was no DTC, a stage
            failed, or the DTC was unchanged (a dedupe skip). A non-None return
            therefore means "a new image was written", which is the signal the
            on-generated notification (a later item) should fire on; a dedupe
            skip deliberately returns ``None`` so it stays silent. Never raises.
        """
        temp = self.temp_path
        output = self.output_path
        if temp is None:
            self._log("Cannot generate kneeboard: DCS temp path is not configured.", logging.ERROR)
            return None
        if output is None:
            self._log("Cannot generate kneeboard: DCS Saved Games path is not configured.", logging.ERROR)
            return None

        if trigger_data and trigger_data.get("aircraft"):
            self._log(f"Trigger aircraft: {trigger_data.get('aircraft')}")

        self._cleanup_legacy_sidecar(output)

        try:
            self._log(f"Scanning temp files in {temp} ...")
            parsed = extract_dtc_from_directory(temp)
            if parsed is None:
                self._log("No DTC data found in temp files; aborting this trigger.", logging.ERROR)
                return None

            processed = process_dtc(parsed)
            self._log(
                f"DTC found: {processed.program_name}, terrain '{processed.terrain}', "
                f"{len(processed.waypoints.entries)} waypoint(s)."
            )

            resolved = resolve_adf_beacon_names(processed, self._resolver)
            if resolved:
                self._log(f"Resolved {resolved} ADF beacon name(s) from beacons.lua.")

            # --- content fingerprint dedupe (after full resolution) ---------
            fingerprint = self._compute_fingerprint(processed)
            if not force and fingerprint == self._last_fingerprint and output.is_file():
                self._log("DTC unchanged; kneeboard not regenerated.")
                return None

            written = render_kneeboard(processed, output)  # logs "Kneeboard generated -> ..."
            # Remember what we just rendered so identical respawns dedupe.
            self._last_fingerprint = fingerprint
            result = Path(written)
            self._notify_generated(result)
            return result

        except DTCProcessingError as exc:
            self._log(f"DTC processing failed: {exc}", logging.ERROR)
            return None
        except KneeboardRenderError as exc:
            self._log(f"Kneeboard render failed: {exc}", logging.ERROR)
            return None
        except Exception as exc:  # noqa: BLE001 - the watcher must never crash
            self._log(f"Failed to generate kneeboard: {exc}", logging.ERROR)
            logger.exception("generate_kneeboard error")
            return None

    def _notify_generated(self, output: Path) -> None:
        """Invoke the on-generated callback, if set, after an actual render.

        Called only when a fresh image was written (never on a dedupe skip).
        The callback must not raise; any exception is caught and logged so a
        misbehaving callback (e.g. a sound failure) cannot disrupt the pipeline.
        """
        if self._on_generated is None:
            return
        try:
            self._on_generated(output)
        except Exception:  # noqa: BLE001 - a callback must never break the pipeline
            logger.exception("on_generated callback raised")

    # --- dedupe helpers ----------------------------------------------------
    @staticmethod
    def _compute_fingerprint(processed: ProcessedDTC) -> str:
        """Return a stable SHA-256 over the resolved DTC's serialised content."""
        payload = json.dumps(processed.to_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _cleanup_legacy_sidecar(self, output: Path) -> None:
        """Delete a stale fingerprint sidecar left by an earlier build (once)."""
        if self._legacy_sidecar_cleaned:
            return
        self._legacy_sidecar_cleaned = True
        legacy = output.with_name(output.stem + LEGACY_SIDECAR_SUFFIX)
        try:
            if legacy.is_file():
                legacy.unlink()
                logger.info("Removed stale dedupe sidecar %s from the kneeboard folder.", legacy.name)
        except OSError as exc:
            logger.debug("Could not remove legacy sidecar %s: %s", legacy, exc)


# ---------------------------------------------------------------------------
# CLI: one-shot manual generation (handy for debugging on the Windows machine)
# ---------------------------------------------------------------------------
def _main() -> int:
    import config as config_module

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    app_config, _report = config_module.load_and_autodetect(save=False)
    watcher = TriggerWatcher(app_config, log_callback=print)
    output = watcher.generate_kneeboard()
    return 0 if output else 1


if __name__ == "__main__":
    raise SystemExit(_main())
