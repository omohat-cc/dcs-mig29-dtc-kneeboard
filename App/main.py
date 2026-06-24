"""GUI entry point for the DCS MiG-29 DTC Kneeboard Utility.

This is component 2's user-facing shell (technical spec, sections 3 and 5). It
wires the already-built backend modules together behind a customtkinter window
and a system-tray icon:

    config            -> load / save / auto-detect the three DCS paths
    hook_manager      -> install or update the DCS Lua hook on startup
    trigger_watcher   -> background daemon thread running the full pipeline
                         (bin_parser -> dtc_processor -> beacon_parser ->
                          kneeboard_renderer)

Design:

* **Startup sequence** (run once the window is drawn): load/create config,
  resolve any undetected paths via a modal first-run dialog, install/update the
  hook, then start the watcher thread.
* **Window behaviour:** the close (X) button minimises to the system tray rather
  than quitting; the tray icon restores the window or quits the app.
* **Threading:** the watcher runs on a daemon thread and never touches Tk. All
  modules log through the standard :mod:`logging` module; a single root handler
  forwards those records to a thread-safe queue, which the GUI drains every
  100 ms onto the read-only status log. Tray callbacks (which fire on pystray's
  own thread) never call Tk directly: they enqueue a callable that the same
  100 ms GUI pump runs on the main loop.

The app is written for the Windows target but uses :mod:`pathlib` / :mod:`os`
throughout, so it imports and runs on macOS for development (where path
auto-detection simply finds nothing and the tray is disabled).

Runtime third-party deps: customtkinter, pystray, Pillow. Everything else is
standard library or a sibling app module.
"""

from __future__ import annotations

import logging
import platform
import queue
import sys
import threading
from pathlib import Path
from tkinter import filedialog
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

import config as config_module
from config import (
    AppConfig,
    PathStatus,
    validate_install_path,
    validate_saved_games_path,
    validate_temp_path,
)
import sound
from app_version import __version__
from hook_manager import install_or_update_hook
from trigger_watcher import TriggerWatcher

# pystray and the icon builder are optional: a failure to import either (e.g. a
# headless box) must not stop the GUI from running, it just disables the tray.
try:
    import pystray  # type: ignore
except Exception:  # noqa: BLE001 - any import failure disables the tray
    pystray = None  # type: ignore

try:
    from app_icon import build_icon_image
except Exception:  # noqa: BLE001
    build_icon_image = None  # type: ignore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WINDOW_TITLE = "DCS MiG-29 DTC Kneeboard Utility"
LOG_POLL_MS = 100        # how often the GUI drains the log queue (spec: 100 ms)
MAX_LOG_LINES = 2000     # trim the status log beyond this to bound memory

# Settings rows, in display order: (config attribute, label, validator).
PATH_FIELDS: List[tuple[str, str, Callable[[Optional[str]], bool]]] = [
    ("dcs_install_path", "DCS Install", validate_install_path),
    ("dcs_saved_games_path", "Saved Games", validate_saved_games_path),
    ("dcs_temp_path", "Temp Dir", validate_temp_path),
]
_VALIDATORS: Dict[str, Callable[[Optional[str]], bool]] = {
    key: validator for key, _label, validator in PATH_FIELDS
}

# Status-chip colours (reused from the kneeboard palette).
COLOUR_OK = "#1F8A3F"
COLOUR_BAD = "#C1273B"
COLOUR_BAD_HOVER = "#9E1F30"  # darker red for the Exit button's hover state
COLOUR_WARN = "#D08700"
COLOUR_IDLE = "gray60"        # neutral grey for the Waiting watch state

# DTC Watch label: maps the watcher's phase ("ground"/"air"/"none") to the
# user-facing text and colour. green Ground / amber Paused / grey Waiting.
DTC_WATCH_STATES: Dict[str, tuple[str, str]] = {
    "ground": ("DTC Watch: Ground", COLOUR_OK),
    "air": ("DTC Watch: Paused", COLOUR_WARN),
    "none": ("DTC Watch: Waiting", COLOUR_IDLE),
}


def _resource_dir() -> Path:
    """Return the directory holding bundled resources (handles PyInstaller)."""
    frozen_dir = getattr(sys, "_MEIPASS", None)
    if frozen_dir:
        return Path(frozen_dir)
    return Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Logging -> GUI bridge
# ---------------------------------------------------------------------------
class QueueLogHandler(logging.Handler):
    """A logging handler that pushes formatted records onto a thread-safe queue.

    Records are enqueued as ``(levelno, line)`` tuples (the level lets the GUI
    colour warnings/errors). Emitting only touches the queue, so it is safe to
    call from the watcher's daemon thread.
    """

    def __init__(self, log_queue: "queue.Queue[tuple[int, str]]") -> None:
        super().__init__()
        self._queue = log_queue
        self._time_formatter = logging.Formatter(datefmt="%H:%M:%S")

    def emit(self, record: logging.LogRecord) -> None:
        """Format ``record`` with an HH:MM:SS stamp and enqueue it."""
        try:
            timestamp = self._time_formatter.formatTime(record, "%H:%M:%S")
            message = record.getMessage()
            if record.levelno >= logging.WARNING:
                line = f"{timestamp}  {record.levelname}: {message}"
            else:
                line = f"{timestamp}  {message}"
            self._queue.put_nowait((record.levelno, line))
        except Exception:  # noqa: BLE001 - logging must never raise into callers
            self.handleError(record)


def configure_logging() -> None:
    """Configure the root logger with console and (best-effort) file handlers.

    The GUI adds its own queue handler separately. Console output is harmless in
    a ``--windowed`` build (no console attached) and useful during development.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    # A --windowed PyInstaller build has no console, so sys.stderr is None; only
    # add the stream handler when there is a real stream to write to.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    try:
        log_path = config_module.app_directory() / "dtc_kneeboard_utility.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        # A read-only install directory is not fatal; carry on without a file log.
        logger.warning("Could not open log file for writing: %s", exc)


# ---------------------------------------------------------------------------
# First-run path-resolution dialog
# ---------------------------------------------------------------------------
class FirstRunDialog(ctk.CTkToplevel):
    """Modal dialog prompting the user to locate any undetected DCS paths.

    Shows one row per path that failed auto-detection, each with a text field, a
    Browse button and a live "Found / Not found" indicator. The chosen values
    are written back onto the shared :class:`~config.AppConfig` when the user
    clicks Save & Continue (or closes the dialog).
    """

    def __init__(self, parent: "DTCKneeboardApp", app_config: AppConfig,
                 missing: List[PathStatus]) -> None:
        super().__init__(parent)
        self.app_config = app_config
        self._missing = list(missing)
        self._entries: Dict[str, ctk.CTkEntry] = {}
        self._indicators: Dict[str, ctk.CTkLabel] = {}
        self._labels: Dict[str, str] = {s.key: s.label for s in self._missing}

        self.title("First-Time Setup")
        self.resizable(False, False)
        self.grid_columnconfigure(1, weight=1)
        self._build()

        # Keep the dialog above the main window and grab focus once it is
        # viewable (grab_set on a not-yet-mapped Toplevel raises on some WMs).
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_done)
        self.after(120, self._grab_focus)

    def _build(self) -> None:
        """Lay out the intro text, one row per missing path, and the OK button."""
        intro = (
            "Some DCS folders could not be detected automatically. "
            "Please locate the folder(s) below, then click Save & Continue."
        )
        ctk.CTkLabel(
            self, text=intro, justify="left", wraplength=620, anchor="w"
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(16, 12))

        for i, status in enumerate(self._missing, start=1):
            ctk.CTkLabel(self, text=status.label, width=120, anchor="w").grid(
                row=i, column=0, sticky="w", padx=(16, 8), pady=8
            )
            entry = ctk.CTkEntry(self, width=380)
            if status.value:
                entry.insert(0, status.value)
            entry.grid(row=i, column=1, sticky="ew", padx=8, pady=8)
            entry.bind("<KeyRelease>", lambda _e, k=status.key: self._update_indicator(k))
            self._entries[status.key] = entry

            ctk.CTkButton(
                self, text="Browse", width=80,
                command=lambda k=status.key: self._browse(k),
            ).grid(row=i, column=2, padx=8, pady=8)

            indicator = ctk.CTkLabel(self, text="Not found", width=90, text_color=COLOUR_BAD)
            indicator.grid(row=i, column=3, sticky="w", padx=(8, 16), pady=8)
            self._indicators[status.key] = indicator
            self._update_indicator(status.key)

        ok_row = len(self._missing) + 1
        ctk.CTkButton(
            self, text="Save & Continue", command=self._on_done
        ).grid(row=ok_row, column=0, columnspan=4, sticky="e", padx=16, pady=(8, 16))

    def _grab_focus(self) -> None:
        """Make the dialog modal and bring it to the front (best-effort)."""
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except Exception:  # noqa: BLE001 - cosmetic; wait_window still blocks
            logger.debug("First-run dialog grab/focus failed", exc_info=True)

    def _update_indicator(self, key: str) -> None:
        """Refresh the Found / Not found chip for one path from its current text."""
        value = self._entries[key].get().strip()
        ok = bool(value) and _VALIDATORS[key](value)
        indicator = self._indicators[key]
        indicator.configure(
            text="Found" if ok else "Not found",
            text_color=COLOUR_OK if ok else COLOUR_BAD,
        )

    def _browse(self, key: str) -> None:
        """Open a directory picker for ``key`` and update its field + indicator."""
        current = self._entries[key].get().strip()
        initial = current if current and Path(current).is_dir() else str(Path.home())
        chosen = filedialog.askdirectory(
            parent=self, initialdir=initial, title=f"Select {self._labels.get(key, 'folder')}"
        )
        if chosen:
            self._entries[key].delete(0, "end")
            self._entries[key].insert(0, chosen)
            self._update_indicator(key)

    def _on_done(self) -> None:
        """Write the chosen values back onto the config and close the dialog."""
        for key, entry in self._entries.items():
            value = entry.get().strip()
            setattr(self.app_config, key, value or None)
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------
class DTCKneeboardApp(ctk.CTk):
    """The main window: settings, status log, tray icon and startup wiring."""

    def __init__(self) -> None:
        super().__init__()

        # NB: never store the AppConfig as ``self.config`` - Tk widgets already
        # expose a ``.config()`` method and shadowing it breaks the toolkit.
        self.app_config: AppConfig = AppConfig()
        self.watcher: Optional[TriggerWatcher] = None
        self.entries: Dict[str, ctk.CTkEntry] = {}
        # The on-demand rebuild button and an in-flight guard. Both are touched
        # only on the Tk thread (the click handler and the queued completion),
        # so a second click cannot start an overlapping rebuild.
        self.regenerate_button: Optional[ctk.CTkButton] = None
        self._regenerating = False
        # Always-visible DTC Watch indicator (Item 5); updated off the watcher
        # thread via the command queue, never touched directly off the Tk loop.
        self.dtc_watch_label: Optional[ctk.CTkLabel] = None
        # Resolved once: the bundled confirmation sound played on each render.
        self._sound_path = sound.kneeboard_sound_path(_resource_dir())

        self._log_queue: "queue.Queue[tuple[int, str]]" = queue.Queue()
        # Tray callbacks fire on pystray's thread; they enqueue a callable here
        # rather than touch Tk directly, and the GUI poll runs it on its own loop.
        self._command_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._raw_log = None  # underlying tk.Text, captured after build (for tags)
        self._quitting = False

        self._tray_icon: Optional["pystray.Icon"] = None
        self._tray_thread: Optional[threading.Thread] = None

        self.title(f"{WINDOW_TITLE}  v{__version__}")
        self.geometry("840x660")
        self.minsize(720, 540)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_settings_section()
        self._build_controls_section()
        self._build_status_section()
        self._set_window_icon()

        # Route every module's logging into the status panel, then start polling.
        self._attach_log_handler()
        self.after(LOG_POLL_MS, self._poll_queues)

        # Close (X) minimises to tray; run the startup sequence once drawn.
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(0, self._run_startup)

    # --- UI construction ---------------------------------------------------
    def _build_settings_section(self) -> None:
        """Build the top settings card: Exit button, three path rows, Save Settings."""
        frame = ctk.CTkFrame(self)
        frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame, text="Settings", anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))

        # Exit (top-right of the card, above the first Browse button): a real
        # quit, unlike the close (X) button which only minimises to the tray.
        # Red sets it apart from the blue Save/Browse buttons and signals that
        # it ends the app. With the single-instance guard, a forgotten tray copy
        # is the usual reason the status log looks "stuck", so quitting cleanly
        # via Exit (rather than leaving copies in the tray) avoids that.
        ctk.CTkButton(
            frame, text="Exit", width=90,
            fg_color=COLOUR_BAD, hover_color=COLOUR_BAD_HOVER,
            command=self._quit_app,
        ).grid(row=0, column=2, sticky="e", padx=(8, 12), pady=(10, 4))

        for i, (key, label, _validator) in enumerate(PATH_FIELDS, start=1):
            ctk.CTkLabel(frame, text=label, width=120, anchor="w").grid(
                row=i, column=0, sticky="w", padx=(12, 8), pady=8
            )
            entry = ctk.CTkEntry(frame)
            entry.grid(row=i, column=1, sticky="ew", padx=8, pady=8)
            self.entries[key] = entry
            ctk.CTkButton(
                frame, text="Browse", width=90,
                command=lambda k=key: self._browse_setting(k),
            ).grid(row=i, column=2, padx=(8, 12), pady=8)

        ctk.CTkButton(
            frame, text="Save Settings", command=self._on_save_settings
        ).grid(row=len(PATH_FIELDS) + 1, column=0, columnspan=3,
               sticky="e", padx=12, pady=(4, 12))

    def _build_controls_section(self) -> None:
        """Build the controls row: the on-demand 'Regenerate Kneeboard' button.

        Regenerate rebuilds the kneeboard from the most recent DTC in the temp
        directory, bypassing the change-detection dedupe (``force=True``), so a
        pilot who edits the DTC in the cockpit after spawning can refresh the
        page on demand. The scan + render runs on a short worker thread (see
        :meth:`_on_regenerate_clicked`) so the Tk loop never freezes.
        """
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        frame.grid_columnconfigure(1, weight=1)

        self.regenerate_button = ctk.CTkButton(
            frame, text="Regenerate Kneeboard", width=180,
            command=self._on_regenerate_clicked,
        )
        self.regenerate_button.grid(row=0, column=0, sticky="w", padx=12, pady=12)

        ctk.CTkLabel(
            frame,
            text="Rebuild the page from the latest in-jet DTC (e.g. after editing it in the cockpit).",
            anchor="w", justify="left", text_color=("gray40", "gray70"),
        ).grid(row=0, column=1, sticky="w", padx=(4, 12), pady=12)

        # DTC Watch indicator: Waiting / Ground / Paused (Item 5). Auto-regenerate
        # watches the DTC temp file while parked; this shows that watch's state.
        text, colour = DTC_WATCH_STATES["none"]
        self.dtc_watch_label = ctk.CTkLabel(
            frame, text=text, width=150, anchor="e",
            font=ctk.CTkFont(weight="bold"), text_color=colour,
        )
        self.dtc_watch_label.grid(row=0, column=2, sticky="e", padx=(4, 12), pady=12)

    def _build_status_section(self) -> None:
        """Build the bottom status card: a read-only, scrolling, timestamped log."""
        frame = ctk.CTkFrame(self)
        frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            frame, text="Status", anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self.log_textbox = ctk.CTkTextbox(frame, wrap="word", state="disabled")
        self.log_textbox.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        # Capture the underlying tk.Text so we can colour warning/error lines.
        self._raw_log = getattr(self.log_textbox, "_textbox", None)
        if self._raw_log is not None:
            try:
                self._raw_log.tag_config("WARNING", foreground=COLOUR_WARN)
                self._raw_log.tag_config("ERROR", foreground=COLOUR_BAD)
            except Exception:  # noqa: BLE001 - colour is a nicety, not required
                logger.debug("Could not configure log colour tags", exc_info=True)

    def _set_window_icon(self) -> None:
        """Set the Windows title-bar / taskbar icon from ``icon.ico`` if present."""
        if platform.system() != "Windows":
            return
        ico = _resource_dir() / "icon.ico"
        if not ico.is_file():
            return
        # customtkinter needs iconbitmap deferred slightly after window creation.
        self.after(250, lambda: self._safe_iconbitmap(str(ico)))

    def _safe_iconbitmap(self, path: str) -> None:
        """Apply a window icon, swallowing the occasional Tk timing error."""
        try:
            self.iconbitmap(path)
        except Exception:  # noqa: BLE001
            logger.debug("Could not set window icon from %s", path, exc_info=True)

    # --- logging bridge ----------------------------------------------------
    def _attach_log_handler(self) -> None:
        """Attach the queue handler to the root logger (INFO and above)."""
        handler = QueueLogHandler(self._log_queue)
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)

    def _poll_queues(self) -> None:
        """Drain queued log lines and tray commands; reschedule every 100 ms.

        This is the single Tk-thread pump: log records become status lines, and
        callables enqueued by the tray thread (restore / quit) are executed here
        so no Tk call ever happens off the main loop.
        """
        try:
            while True:
                levelno, line = self._log_queue.get_nowait()
                self._append_log(levelno, line)
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001 - never let the poll loop die
            logger.debug("Log drain error", exc_info=True)

        try:
            while True:
                command = self._command_queue.get_nowait()
                command()
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001 - one bad command must not stop the pump
            logger.debug("Command execution error", exc_info=True)

        if not self._quitting:
            self.after(LOG_POLL_MS, self._poll_queues)

    def _append_log(self, levelno: int, line: str) -> None:
        """Append one line to the read-only log, colouring warnings/errors."""
        tag = "ERROR" if levelno >= logging.ERROR else (
            "WARNING" if levelno >= logging.WARNING else None
        )
        try:
            self.log_textbox.configure(state="normal")
            if tag and self._raw_log is not None:
                self._raw_log.insert("end", line + "\n", tag)
            else:
                self.log_textbox.insert("end", line + "\n")
            self._trim_log()
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")
        except Exception:  # noqa: BLE001 - a logging hiccup must not crash the GUI
            logger.debug("Could not append to status log", exc_info=True)

    def _trim_log(self) -> None:
        """Drop the oldest lines once the log exceeds :data:`MAX_LOG_LINES`."""
        if self._raw_log is None:
            return
        try:
            line_count = int(self._raw_log.index("end-1c").split(".")[0])
            if line_count > MAX_LOG_LINES:
                self._raw_log.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        except Exception:  # noqa: BLE001
            pass

    # --- startup sequence --------------------------------------------------
    def _run_startup(self) -> None:
        """Load config, resolve paths, install the hook and start the watcher."""
        logger.info("Starting %s v%s", WINDOW_TITLE, __version__)
        self.app_config, report = config_module.load_and_autodetect()
        self._refresh_entries_from_config()

        if report.missing:
            labels = ", ".join(s.label for s in report.missing)
            logger.info("%d path(s) need manual entry (%s); opening setup dialog.",
                        len(report.missing), labels)
            dialog = FirstRunDialog(self, self.app_config, report.missing)
            self.wait_window(dialog)  # blocks until the dialog is closed
            if self._quitting:
                return  # user quit from the tray while the dialog was open
            config_module.save_config(self.app_config)
            self._refresh_entries_from_config()

        self._install_hook()
        self._start_watcher()
        logger.info("Startup complete.")

    def _refresh_entries_from_config(self) -> None:
        """Populate the settings fields from the current config values."""
        for key, _label, _validator in PATH_FIELDS:
            value = getattr(self.app_config, key, None)
            entry = self.entries[key]
            entry.delete(0, "end")
            if value:
                entry.insert(0, value)

    def _install_hook(self) -> None:
        """Install or update the DCS Lua hook if the Saved Games path is valid."""
        saved_games = self.app_config.dcs_saved_games_path
        if not saved_games or not validate_saved_games_path(saved_games):
            logger.warning("DCS hook not installed: Saved Games path is not set or invalid.")
            return
        install_or_update_hook(saved_games)  # logs its own INFO/ERROR result

    def _start_watcher(self) -> None:
        """(Re)create and start the trigger watcher from the current config."""
        if self.watcher is not None and self.watcher.is_running():
            self.watcher.stop()
        self.watcher = TriggerWatcher(
            self.app_config,
            on_generated=self._on_kneeboard_generated,
            on_phase_change=self._on_phase_change,
        )
        # Reset the indicator to Waiting whenever the watcher is (re)started.
        self._apply_watch_label("none")
        self.watcher.start()  # logs "Watching..." or an error if paths are unset

    def _on_kneeboard_generated(self, path: Path) -> None:
        """Play the confirmation sound after a successful kneeboard render.

        Passed to the watcher as its ``on_generated`` callback, so it runs on
        the watcher's daemon thread and only when a fresh image was written
        (never on a dedupe skip). Playback is non-blocking (winsound SND_ASYNC)
        and never touches Tk, so it is safe to call here directly without
        marshalling onto the GUI loop; a sound failure is swallowed by
        :func:`sound.play_sound`.
        """
        logger.info("Kneeboard generated; playing confirmation sound (%s).", self._sound_path)
        sound.play_sound(self._sound_path)

    def _on_phase_change(self, phase: str) -> None:
        """Watcher phase callback: marshal the label update onto the Tk loop.

        Runs on the watcher's daemon thread, so it must not touch Tk directly
        (gotcha 3); it enqueues the update onto the command queue, which the
        100 ms GUI pump drains on the main thread.
        """
        self._command_queue.put(lambda: self._apply_watch_label(phase))

    def _apply_watch_label(self, phase: str) -> None:
        """Set the DTC Watch label text/colour from the phase (Tk-thread only)."""
        label = self.dtc_watch_label
        if label is None:
            return
        text, colour = DTC_WATCH_STATES.get(phase, DTC_WATCH_STATES["none"])
        try:
            label.configure(text=text, text_color=colour)
        except Exception:  # noqa: BLE001 - the label is cosmetic, never fatal
            logger.debug("Could not update the DTC watch label", exc_info=True)

    # --- regenerate action -------------------------------------------------
    def _on_regenerate_clicked(self) -> None:
        """Handle the Regenerate button: run a forced rebuild off the Tk thread.

        Disables the button, then runs :meth:`TriggerWatcher.generate_kneeboard`
        with ``force=True`` (bypassing the dedupe, since the user explicitly
        asked for a fresh page) on a short daemon worker thread so the temp-dir
        scan and render never freeze the GUI. The outcome is marshalled back
        onto the Tk loop via the command queue (:meth:`_on_regenerate_done`).
        """
        if self._regenerating:
            return  # a rebuild is already in flight; the button is disabled
        watcher = self.watcher
        if watcher is None:
            logger.warning("Cannot regenerate yet: the watcher is still starting up.")
            return

        self._regenerating = True
        self._set_regenerate_enabled(False)
        logger.info("Manual regenerate requested; rebuilding the kneeboard...")

        worker = threading.Thread(
            target=self._regenerate_worker, args=(watcher,),
            name="dtc-regenerate", daemon=True,
        )
        worker.start()

    def _regenerate_worker(self, watcher: TriggerWatcher) -> None:
        """Run the forced rebuild on a worker thread; marshal the result back.

        Runs off the Tk loop. ``generate_kneeboard`` already catches and logs
        its own failures and returns ``None``, but it is wrapped defensively so
        that even an unexpected error still re-enables the button rather than
        stranding it. The outcome is handed to :meth:`_on_regenerate_done` via
        the command queue, which the 100 ms GUI pump drains on the main thread.
        On success the confirmation sound is played by the watcher's existing
        on-generated callback (:meth:`_on_kneeboard_generated`), not here.
        """
        result: Optional[Path] = None
        try:
            result = watcher.generate_kneeboard(force=True)
        except Exception:  # noqa: BLE001 - a worker crash must not strand the button
            logger.exception("Manual regenerate failed unexpectedly.")
            result = None
        self._command_queue.put(lambda: self._on_regenerate_done(result))

    def _on_regenerate_done(self, result: Optional[Path]) -> None:
        """Re-enable the button and report the outcome (runs on the Tk loop).

        On success the path is already logged by the renderer and the sound is
        played by the on-generated callback, so here we re-enable the button and
        confirm the manual action. On failure (``None``) we surface a friendly
        summary; ``generate_kneeboard`` has already logged the specific reason
        (temp path unset, no DTC ``.bin`` found, or a processing/render error)
        on the line just above.
        """
        self._regenerating = False
        self._set_regenerate_enabled(True)
        if result is None:
            logger.warning(
                "Regenerate produced no kneeboard. Spawn into a MiG-29 in DCS "
                "first, then regenerate (see the message above for the reason)."
            )
        else:
            logger.info("Manual regenerate complete -> %s", result)

    def _set_regenerate_enabled(self, enabled: bool) -> None:
        """Enable or disable the Regenerate button (Tk-thread only, never fatal).

        While a rebuild is in flight the button is disabled and relabelled, so a
        second click cannot start an overlapping rebuild. Button state is
        cosmetic, so any Tk error here is logged at debug level, not raised.
        """
        button = self.regenerate_button
        if button is None:
            return
        try:
            button.configure(
                state="normal" if enabled else "disabled",
                text="Regenerate Kneeboard" if enabled else "Regenerating...",
            )
        except Exception:  # noqa: BLE001 - button state is cosmetic, never fatal
            logger.debug("Could not update the regenerate button state", exc_info=True)

    # --- settings actions --------------------------------------------------
    def _browse_setting(self, key: str) -> None:
        """Open a directory picker for a settings row and fill its field."""
        current = self.entries[key].get().strip()
        initial = current if current and Path(current).is_dir() else str(Path.home())
        label = next((lbl for k, lbl, _v in PATH_FIELDS if k == key), "folder")
        chosen = filedialog.askdirectory(
            parent=self, initialdir=initial, title=f"Select {label}"
        )
        if chosen:
            self.entries[key].delete(0, "end")
            self.entries[key].insert(0, chosen)

    def _on_save_settings(self) -> None:
        """Persist the edited paths, reinstall the hook and restart the watcher."""
        for key, _label, _validator in PATH_FIELDS:
            value = self.entries[key].get().strip()
            setattr(self.app_config, key, value or None)

        if config_module.save_config(self.app_config):
            logger.info("Settings saved to %s", config_module.default_config_path())
        else:
            logger.error("Failed to save settings.")

        self._warn_invalid_paths()
        self._install_hook()
        self._start_watcher()

    def _warn_invalid_paths(self) -> None:
        """Log a warning for each configured path that fails validation."""
        for key, label, validator in PATH_FIELDS:
            value = getattr(self.app_config, key, None)
            if not value:
                logger.warning("%s path is empty.", label)
            elif not validator(value):
                logger.warning("%s path does not validate: %s", label, value)

    # --- system tray -------------------------------------------------------
    @staticmethod
    def _tray_supported() -> bool:
        """True if a system tray can be run on this platform with our deps.

        pystray's macOS backend must run on the main thread (it drives the Cocoa
        loop), which conflicts with Tk owning the main thread, so the tray is
        only enabled on Windows (the target) and Linux.
        """
        return (
            pystray is not None
            and build_icon_image is not None
            and platform.system() in ("Windows", "Linux")
        )

    def _setup_tray(self) -> None:
        """Create and start the system-tray icon on a daemon thread."""
        if not self._tray_supported():
            logger.info("System tray unavailable on this platform; the close button will quit.")
            return
        try:
            image = build_icon_image(64)  # type: ignore[misc]
            menu = pystray.Menu(  # type: ignore[union-attr]
                pystray.MenuItem("Restore", self._on_tray_restore, default=True),
                pystray.MenuItem("Quit", self._on_tray_quit),
            )
            self._tray_icon = pystray.Icon(  # type: ignore[union-attr]
                "dtc_kneeboard", image, WINDOW_TITLE, menu
            )
        except Exception as exc:  # noqa: BLE001 - degrade to no-tray on any failure
            logger.warning("Could not create system-tray icon: %s", exc)
            self._tray_icon = None
            return

        self._tray_thread = threading.Thread(
            target=self._run_tray, name="dtc-tray", daemon=True
        )
        self._tray_thread.start()
        logger.info("System tray icon active. Close (X) minimises here.")

    def _run_tray(self) -> None:
        """Run the tray icon's event loop; clears the icon on failure."""
        try:
            self._tray_icon.run()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            logger.exception("System tray icon stopped unexpectedly; close will now quit.")
            self._tray_icon = None

    def _on_tray_restore(self, _icon: object = None, _item: object = None) -> None:
        """Tray 'Restore' (and left-click): queue a restore for the Tk loop."""
        self._command_queue.put(self._restore_window)

    def _on_tray_quit(self, _icon: object = None, _item: object = None) -> None:
        """Tray 'Quit': queue a full shutdown for the Tk loop."""
        self._command_queue.put(self._quit_app)

    def _restore_window(self) -> None:
        """Show and focus the main window."""
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:  # noqa: BLE001
            logger.debug("Could not restore window", exc_info=True)

    # --- shutdown ----------------------------------------------------------
    def _tray_alive(self) -> bool:
        """True while the tray thread is running and able to restore the window."""
        return self._tray_thread is not None and self._tray_thread.is_alive()

    def _on_close(self) -> None:
        """Close (X): minimise to tray if available, otherwise quit."""
        if self._tray_alive():
            self.withdraw()
            logger.info(
                "Minimised to the system tray. Use the Exit button (or the tray's "
                "Quit) to close it fully."
            )
        else:
            self._quit_app()

    def _quit_app(self) -> None:
        """Stop the watcher and tray, then tear down the window (idempotent)."""
        if self._quitting:
            return
        self._quitting = True
        logger.info("Shutting down...")
        try:
            if self.watcher is not None:
                self.watcher.stop()
        except Exception:  # noqa: BLE001
            logger.exception("Error stopping watcher during shutdown.")
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
        except Exception:  # noqa: BLE001
            logger.exception("Error stopping tray icon during shutdown.")
        try:
            self.destroy()
        except Exception:  # noqa: BLE001
            pass

    # --- run ---------------------------------------------------------------
    def run(self) -> None:
        """Start the tray, then enter the Tk main loop (blocks until quit)."""
        self._setup_tray()
        self.mainloop()


# ---------------------------------------------------------------------------
# Single-instance guard
# ---------------------------------------------------------------------------
# The named mutex handle of the first instance, kept for the whole process
# lifetime so the name persists and a second launch can detect it. Module-level
# so it is never garbage-collected while the app runs.
_single_instance_handle: object = None
SINGLE_INSTANCE_MUTEX = "DCS_MiG29_DTC_Kneeboard_Utility_SingleInstance"


def _acquire_single_instance() -> bool:
    """Return True if this is the only running instance, else False.

    Uses a named Windows mutex: the first instance creates it and keeps the
    handle open, so a second launch finds it already exists. The OS releases the
    mutex when the owning process ends, so there is no stale-lock problem.
    Windows-only; on other platforms it always returns True (the GUI runs only
    on Windows, so the dev machine never needs the guard). Never raises: on any
    error it returns True, so a detection failure can never stop the app.
    """
    global _single_instance_handle
    if platform.system() != "Windows":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        error_already_exists = 183
        kernel32 = ctypes.windll.kernel32
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        create_mutex.restype = wintypes.HANDLE

        handle = create_mutex(None, False, SINGLE_INSTANCE_MUTEX)
        if not handle:
            return True  # could not create the mutex; do not block startup
        if kernel32.GetLastError() == error_already_exists:
            return False
        _single_instance_handle = handle  # keep alive for this process
        return True
    except Exception:  # noqa: BLE001 - a guard failure must never stop the app
        logger.warning("Single-instance check failed; starting anyway.", exc_info=True)
        return True


def _notify_already_running() -> None:
    """Tell the user the app is already running (the caller then exits).

    Shows a native Windows message box (the app is built ``--windowed``, so
    there is no console) and logs the event. Best-effort and never raises.
    """
    logger.warning("Another instance is already running; exiting this one.")
    if platform.system() != "Windows":
        return
    message = (
        "DCS MiG-29 DTC Kneeboard Utility is already running.\n\n"
        "Look for its icon in the system tray, near the clock. The close (X) "
        "button only minimises the app there; use the Exit button in its window "
        "to quit it."
    )
    try:
        import ctypes

        mb_iconinformation = 0x40
        mb_setforeground = 0x10000
        ctypes.windll.user32.MessageBoxW(
            0, message, "Already running", mb_iconinformation | mb_setforeground
        )
    except Exception:  # noqa: BLE001 - the notice is best-effort
        logger.debug("Could not show the 'already running' message box.", exc_info=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    """Enforce single-instance, configure logging, build the window and run."""
    configure_logging()
    if not _acquire_single_instance():
        _notify_already_running()
        return 0
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    try:
        app = DTCKneeboardApp()
        app.run()
    except Exception as exc:  # noqa: BLE001 - last-resort guard for a clean exit
        logger.exception("Fatal error in the GUI: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
