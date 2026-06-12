"""Programmatic application / system-tray icon for the DTC Kneeboard Utility.

Kept deliberately free of any GUI-toolkit dependency (Pillow only) so that:

* :mod:`main` can build the tray icon in memory at runtime without shipping a
  separate image asset, and
* :mod:`generate_icon` (and the PyInstaller build) can write ``icon.ico`` /
  ``icon.png`` to disk for the window title-bar and the exe.

The icon is a dark rounded "kneeboard" tile with a brand-yellow clip band and a
white "29", using the palette from the kneeboard visual-design spec (section 4).

Only Pillow plus the standard library is required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw, ImageFont

# Palette (kneeboard visual-design spec, section 4), as RGBA.
INK: Tuple[int, int, int, int] = (0x15, 0x17, 0x1A, 255)        # tile background
BRAND_YELLOW: Tuple[int, int, int, int] = (0xF7, 0xD0, 0x46, 255)  # clip band
WHITE: Tuple[int, int, int, int] = (0xFF, 0xFF, 0xFF, 255)      # "29" glyphs
TRANSPARENT: Tuple[int, int, int, int] = (0, 0, 0, 0)

# Default sizes embedded in a multi-resolution .ico (Windows picks per context).
ICO_SIZES: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

# Bundled display font used for the "29"; falls back to Pillow's default if the
# TTF cannot be located (e.g. running outside the source tree).
_GLYPH_FONT_FILES = ("BarlowCondensed-ExtraBold.ttf", "BarlowCondensed-Bold.ttf")


def _resource_dir() -> Path:
    """Return the directory holding bundled resources (handles PyInstaller)."""
    frozen_dir = getattr(sys, "_MEIPASS", None)
    if frozen_dir:
        return Path(frozen_dir)
    return Path(__file__).resolve().parent


def _load_glyph_font(size: int) -> ImageFont.ImageFont:
    """Load Barlow Condensed at ``size`` px, or Pillow's default font.

    The bundled font gives the "29" its condensed display look; the default
    font is an acceptable fallback for the small tray icon if the TTF is absent.
    """
    fonts_dir = _resource_dir() / "fonts"
    for filename in _GLYPH_FONT_FILES:
        candidate = fonts_dir / filename
        try:
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_icon_image(size: int = 64) -> Image.Image:
    """Render the app icon at ``size`` x ``size`` px as an RGBA image.

    Args:
        size: Square edge length in pixels.

    Returns:
        A new RGBA :class:`PIL.Image.Image` with a transparent background.
    """
    image = Image.new("RGBA", (size, size), TRANSPARENT)
    draw = ImageDraw.Draw(image)

    pad = max(1, round(size * 0.06))
    radius = max(2, round(size * 0.20))
    draw.rounded_rectangle(
        [pad, pad, size - 1 - pad, size - 1 - pad], radius=radius, fill=INK
    )

    # Yellow "clip" band near the top, evoking a kneeboard page binder.
    band_inset = round(size * 0.20)
    band_top = pad + max(1, round(size * 0.09))
    band_h = max(2, round(size * 0.15))
    draw.rounded_rectangle(
        [pad + band_inset, band_top, size - 1 - pad - band_inset, band_top + band_h],
        radius=max(1, round(size * 0.05)),
        fill=BRAND_YELLOW,
    )

    # "29", centred in the lower portion of the tile.
    font = _load_glyph_font(round(size * 0.48))
    text = "29"
    box = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = box[2] - box[0], box[3] - box[1]
    text_x = (size - text_w) / 2 - box[0]
    text_y = (size - text_h) / 2 - box[1] + round(size * 0.09)
    draw.text((text_x, text_y), text, font=font, fill=WHITE)

    return image


def save_png(path: Path | str, size: int = 256) -> Path:
    """Write a single-resolution PNG of the icon and return its path."""
    out = Path(path)
    build_icon_image(size).save(out, format="PNG")
    return out


def save_ico(path: Path | str, sizes: Tuple[int, ...] = ICO_SIZES) -> Path:
    """Write a multi-resolution Windows ``.ico`` and return its path.

    Pillow downscales the 256 px master to each requested size, so Windows can
    pick the crispest variant for the taskbar, tray and Alt-Tab.
    """
    out = Path(path)
    master = build_icon_image(max(sizes))
    master.save(out, format="ICO", sizes=[(s, s) for s in sizes])
    return out
