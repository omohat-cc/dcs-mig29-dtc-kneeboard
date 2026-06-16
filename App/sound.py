"""Play the short confirmation sound when a kneeboard is generated.

Audible playback is Windows-only, via the standard-library :mod:`winsound`
module with ``SND_ASYNC`` so the call returns immediately and never blocks the
caller (safe to call from the watcher's daemon thread). On other platforms it
is a logged no-op, which keeps the watcher and GUI importable and runnable on
macOS for development. Any playback failure is logged and swallowed, never
raised, so a sound problem can never disrupt the pipeline or the GUI.

Runtime third-party dependencies are unchanged: :mod:`winsound` is part of the
standard library on Windows.
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

logger = logging.getLogger(__name__)

# Bundled asset location, relative to the resource directory (the App folder in
# development, or ``sys._MEIPASS`` in a PyInstaller build). The PyInstaller spec
# bundles the whole ``sounds`` folder, so this resolves in both cases.
SOUND_SUBPATH = ("sounds", "kneeboard_generated.wav")


def kneeboard_sound_path(resource_dir: Path) -> Path:
    """Return the bundled confirmation-sound path under ``resource_dir``."""
    return resource_dir.joinpath(*SOUND_SUBPATH)


def play_sound(path: Path) -> bool:
    """Play a WAV file asynchronously on Windows. Never raises.

    Args:
        path: The WAV file to play. ``winsound`` only supports PCM WAV files.

    Returns:
        ``True`` if playback was started; ``False`` if it was skipped (not
        Windows, or the file is missing) or a playback error was swallowed.
    """
    if platform.system() != "Windows":
        logger.debug("Sound playback skipped (not Windows): %s", path)
        return False
    try:
        if not path.is_file():
            logger.warning("Confirmation sound not found at %s; skipping playback.", path)
            return False
        # Imported lazily: winsound is Windows-only, so a top-level import would
        # break this module on the macOS dev machine.
        import winsound

        # SND_ASYNC returns immediately; SND_NODEFAULT suppresses the system
        # beep if the file cannot be played for any reason.
        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - a sound failure must never disrupt the app
        logger.warning("Could not play confirmation sound %s: %s", path, exc, exc_info=True)
        return False
