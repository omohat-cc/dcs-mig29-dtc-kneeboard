"""Generate the application icon assets (``icon.ico`` and ``icon.png``).

Run this once before packaging so the PyInstaller build can embed a Windows
icon and the GUI can show a title-bar icon:

    python generate_icon.py

The drawing itself lives in :mod:`app_icon` (Pillow only, no GUI deps) so the
running app can also build the same icon in memory for the system tray.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app_icon import save_ico, save_png

logger = logging.getLogger(__name__)


def main() -> int:
    """Write ``icon.ico`` and ``icon.png`` next to this script."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    here = Path(__file__).resolve().parent
    try:
        ico = save_ico(here / "icon.ico")
        png = save_png(here / "icon.png")
    except OSError as exc:
        logger.error("Could not write icon assets: %s", exc)
        return 1
    logger.info("Wrote %s", ico)
    logger.info("Wrote %s", png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
