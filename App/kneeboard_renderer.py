"""Pillow-based kneeboard image generator for the DCS MiG-29 DTC Kneeboard Utility.

This module renders a 1536x2048 JPEG kneeboard summarising one DTC program. It
consumes the resolved data produced by :mod:`dtc_processor` (either a
``ProcessedDTC`` instance or its ``to_dict()`` form) and lays it out on the
fixed "Omar's Grid":

    [            Header (full width)              ]
    [ Waypoints | Airdromes | RSBN  ]              (row 1, 3 equal cards)
    [ ADF (cols 1-2)        | Radio ]              (row 2, Radio spans rows 2-3)
    [ SPO-15    | CMDS      | Radio ]              (row 3)

Every coordinate, colour, font and size below is taken from the kneeboard
visual-design specification (technical spec section 4) and the "Pillow
Replication Spec" (design PDF pages 3-4), then reconciled against the locked
reference render so the output is pixel-faithful.

The layout structure is fixed for cockpit muscle memory; only the row *content*
varies. Sections with no configured data render a "NO CONFIG" empty state with
their header band intact. Navigation triplets (Waypoints/Airdromes/RSBN) always
show three slots, padding unused slots with a greyed placeholder.

Only Pillow (plus the standard library) is required at runtime.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class KneeboardRenderError(Exception):
    """Raised when a kneeboard image cannot be generated or saved."""


# ===========================================================================
# Canvas, palette and frame constants (from the Pillow Replication Spec)
# ===========================================================================

CANVAS_W, CANVAS_H = 1536, 2048
BG = (255, 255, 255)  # true white, per spec (cream paper is too low-contrast)

# Colour palette (hex from the spec, as RGB tuples).
INK = (0x15, 0x17, 0x1A)        # primary text, header fills, structural rules
INK_2 = (0x2C, 0x2F, 0x35)      # secondary text, eyebrow labels, modulation
INK_3 = (0x8A, 0x8E, 0x95)      # "Off" status, NO CONFIG, placeholder slots
COL_HEADER = (0xEC, 0xEE, 0xF1)  # column-header strip fill (cool light grey)
RULE = (0xC8, 0xC2, 0xB6)       # dotted dividers
DATA_RED = (0xC1, 0x27, 0x3B)   # SPO-15 Lock
DATA_GREEN = (0x1F, 0x8A, 0x3F)  # SPO-15 On
BRAND_YELLOW = (0xF7, 0xD0, 0x46)  # palette token (DimOn pennant); unused in this layout
WHITE = (255, 255, 255)

# White at reduced opacity over the ink header band, pre-blended to opaque RGB
# (Pillow draws onto an RGB canvas, so we flatten the alpha here).
SUBTITLE_ON_DARK = (185, 185, 186)  # white @ 0.70 over INK
COUNT_ON_DARK = (197, 197, 198)     # white @ 0.75 over INK

MARGIN = 48
GAP = 24
STRUCT_BORDER = 2   # outer / structural border weight
INNER_DIVIDER = 1   # inner divider / col-header underline
DOT_ON, DOT_OFF = 6, 6  # dotted rule dash pattern

CONTENT_RIGHT = CANVAS_W - MARGIN   # 1488
CONTENT_BOTTOM = CANVAS_H - MARGIN  # 2000

# --- Three-column grid (each column 464 px wide, 24 px gaps) ---------------
COL_W = (CONTENT_RIGHT - MARGIN - 2 * GAP) // 3  # 464
COL1_X0, COL1_X1 = MARGIN, MARGIN + COL_W                       # 48 .. 512
COL2_X0, COL2_X1 = COL1_X1 + GAP, COL1_X1 + GAP + COL_W         # 536 .. 1000
COL3_X0, COL3_X1 = COL2_X1 + GAP, COL2_X1 + GAP + COL_W         # 1024 .. 1488
ADF_X0, ADF_X1 = COL1_X0, COL2_X1                               # 48 .. 1000 (cols 1-2)

# --- Vertical bands (measured against the reference render) ----------------
HEADER_TOP = MARGIN          # 48
HEADER_BOTTOM = 227          # header strip content bottom (meta box bottom edge)
ROW1_TOP = HEADER_BOTTOM + GAP                 # 251
ROW1_H = 420                                   # nav triplet, fixed per spec
ROW1_BOT = ROW1_TOP + ROW1_H                   # 671
ROW2_TOP = ROW1_BOT + GAP                      # 695
# Rows 2 and 3 split the remaining height equally ("1fr each").
_REST = CONTENT_BOTTOM - ROW2_TOP - GAP        # 1281
ROW2_BOT = ROW2_TOP + _REST // 2               # 1335
ROW3_TOP = ROW2_BOT + GAP                      # 1359
ROW3_BOT = CONTENT_BOTTOM                       # 2000
RADIO_TOP, RADIO_BOT = ROW2_TOP, ROW3_BOT      # 695 .. 2000 (spans rows 2-3)

HEADER_BAND_H = 64    # section card header band
COL_HEADER_H = 40     # column-header strip
ROW_PAD_X = 18        # row inner padding, left/right
ROW_PAD_Y = 8         # row inner padding, top/bottom

# Rows use a fixed height (not stretched to fill the card): a uniform pitch
# gives every section the same vertical rhythm. Sections with fewer rows than
# the card can hold simply leave white space at the bottom, exactly as the
# reference render does. Radio is compact so all 20 channels fit its column.
DEFAULT_ROW_H = 81
RADIO_ROW_H = 53

# --- Font sizes ------------------------------------------------------------
# Calibrated to the locked reference render. The written px values in the
# Pillow Replication Spec are ~1.2x smaller than the design render they
# describe; these sizes match the render (the design the kneeboard must look
# like) while every frame/grid measurement still follows the spec exactly.
FS_TAG = 52            # page tag "MIG-29A"
FS_TITLE = 66          # "DTC · PROGRAM N"
FS_PAGE_SUB = 22       # header subtitle line
FS_META_LABEL = 14     # THEATRE / GENERATED labels
FS_META_VALUE = 25     # meta values (mono)
FS_SECTION_TITLE = 33  # card header title
FS_SECTION_SUB = 17    # card header subtitle (dimmed)
FS_SECTION_COUNT = 21  # card header right-side count
FS_COL_LABEL = 15      # column-header strip labels
FS_SLOT = 28           # slot tags (WPT1, CH 00, ...)
FS_NAME = 30           # resolved names
FS_VALUE = 28          # secondary mono values (freq, runway, channel, CMDS value)
FS_MOD = 21            # modulation tag (AM/FM)
FS_DESC = 27           # SPO-15 threat description
FS_SPO_GLYPH = 41      # SPO-15 Cyrillic glyph (Oswald)
FS_SPO_LATIN = 19      # SPO-15 Latin equivalent in parens
FS_SPO_SUB = 15        # SPO-15 threat subtitle (mono, dimmed; sized to fit 1 line)
FS_CHIP = 21           # status chip text
FS_NOCONFIG = 39       # NO CONFIG empty-state label
FS_HINT = 17           # "... NOT PROGRAMMED" hint

# ===========================================================================
# Fonts
# ===========================================================================


def _resource_dir() -> Path:
    """Return the directory holding bundled resources (handles PyInstaller)."""
    frozen_dir = getattr(sys, "_MEIPASS", None)
    if frozen_dir:
        return Path(frozen_dir)
    return Path(__file__).resolve().parent


FONTS_DIR = _resource_dir() / "fonts"

# Logical font roles -> bundled TTF filenames.
_FONT_FILES = {
    "bc_xbold": "BarlowCondensed-ExtraBold.ttf",  # display 800
    "bc_bold": "BarlowCondensed-Bold.ttf",        # display 700
    "barlow_med": "Barlow-Medium.ttf",            # body 500
    "barlow_semi": "Barlow-SemiBold.ttf",         # body 600
    "barlow_bold": "Barlow-Bold.ttf",             # body 700
    "jb_med": "JetBrainsMono-Medium.ttf",         # mono 500
    "jb_bold": "JetBrainsMono-Bold.ttf",          # mono 700
    "oswald_bold": "Oswald-Bold.ttf",             # Cyrillic fallback for SPO-15
}


class FontBook:
    """Lazily loads and caches the bundled TTF faces at requested sizes.

    Barlow Condensed has no Cyrillic glyphs, so the SPO-15 threat letters use
    Oswald (the spec-named display fallback), which is condensed, bold and
    covers Cyrillic.
    """

    def __init__(self, fonts_dir: Path = FONTS_DIR) -> None:
        self._dir = fonts_dir
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        missing = [
            name for name, fn in _FONT_FILES.items() if not (fonts_dir / fn).is_file()
        ]
        if missing:
            raise KneeboardRenderError(
                "Missing bundled font(s) in "
                f"'{fonts_dir}': {', '.join(_FONT_FILES[m] for m in missing)}"
            )

    def get(self, role: str, size: int) -> ImageFont.FreeTypeFont:
        """Return the cached face for ``role`` at ``size`` px."""
        key = (role, size)
        font = self._cache.get(key)
        if font is None:
            try:
                font = ImageFont.truetype(str(self._dir / _FONT_FILES[role]), size)
            except OSError as exc:
                raise KneeboardRenderError(
                    f"Could not load font '{role}' at {size}px: {exc}"
                ) from exc
            self._cache[key] = font
        return font


# ===========================================================================
# Low-level drawing helpers
# ===========================================================================


def _track_px(em_fraction: float, size: int) -> float:
    """Convert a CSS-style letter-spacing (in em) to pixels at ``size``."""
    return em_fraction * size


def _text_width(font: ImageFont.FreeTypeFont, text: str, tracking: float = 0.0) -> float:
    """Total advance width of ``text`` including inter-character ``tracking``."""
    if not text:
        return 0.0
    width = sum(font.getlength(ch) for ch in text)
    return width + tracking * (len(text) - 1)


def _draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    *,
    tracking: float = 0.0,
    anchor: str = "lm",
) -> float:
    """Draw ``text`` with optional letter-spacing; return its total width.

    ``anchor`` follows Pillow's two-letter convention but is applied to the
    whole tracked string (horizontal: l/m/r, vertical: a/m/s/etc.). Drawing one
    character at a time is what lets us honour ``tracking`` (Pillow has no
    native letter-spacing).
    """
    x, y = xy
    total = _text_width(font, text, tracking)
    h_anchor = anchor[0]
    if h_anchor == "m":
        x -= total / 2
    elif h_anchor == "r":
        x -= total
    v_anchor = anchor[1] if len(anchor) > 1 else "m"
    char_anchor = "l" + v_anchor
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor=char_anchor)
        x += font.getlength(ch) + tracking
    return total


def _dotted_hline(
    draw: ImageDraw.ImageDraw, x0: float, x1: float, y: float,
    fill: tuple[int, int, int] = RULE, width: int = 1,
) -> None:
    """Draw a horizontal dotted rule (6px on / 6px off)."""
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + DOT_ON, x1), y)], fill=fill, width=width)
        x += DOT_ON + DOT_OFF


def _dotted_vline(
    draw: ImageDraw.ImageDraw, x: float, y0: float, y1: float,
    fill: tuple[int, int, int] = RULE, width: int = 1,
) -> None:
    """Draw a vertical dotted rule (6px on / 6px off)."""
    y = y0
    while y < y1:
        draw.line([(x, y), (x, min(y + DOT_ON, y1))], fill=fill, width=width)
        y += DOT_ON + DOT_OFF


def _wrap_to_width(
    font: ImageFont.FreeTypeFont, text: str, max_w: float, max_lines: int = 2,
    *, break_hyphens: bool = True,
) -> list[str]:
    """Wrap ``text`` to fit ``max_w``, breaking on spaces (and hyphens).

    Returns up to ``max_lines`` lines; the final line is ellipsised if the text
    still overflows. Long resolved names (e.g. "Krasnodar-Center") wrap to two
    lines in narrow columns, matching the reference render. Set
    ``break_hyphens=False`` to break on spaces only, so hyphenated tokens like
    "F-14" stay intact (used for the threat-description subtitles).
    """
    if font.getlength(text) <= max_w:
        return [text]

    # Build break candidates. With break_hyphens, keep a hyphen on the preceding
    # fragment so a break can follow it; otherwise only spaces are break points.
    break_chars = " -" if break_hyphens else " "
    tokens: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in break_chars:
            tokens.append(buf)
            buf = ""
    if buf:
        tokens.append(buf)

    lines: list[str] = []
    current = ""
    for i, tok in enumerate(tokens):
        candidate = current + tok
        if not current or font.getlength(candidate.rstrip()) <= max_w:
            current = candidate
        else:
            lines.append(current.rstrip())
            if len(lines) == max_lines - 1:
                # No room for more breaks: dump the remaining tokens here.
                current = "".join(tokens[i:])
                break
            current = tok
    lines.append(current.rstrip())

    # Hard-truncate the last line with an ellipsis if it is still too wide.
    if font.getlength(lines[-1]) > max_w:
        trimmed = lines[-1]
        while trimmed and font.getlength(trimmed + "…") > max_w:
            trimmed = trimmed[:-1]
        lines[-1] = trimmed + "…"
    return lines[:max_lines]


def _draw_cell_name(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: float,
    cy: float,
    max_w: float,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    align: str = "left",
    *,
    sub_text: Optional[str] = None,
    sub_font: Optional[ImageFont.FreeTypeFont] = None,
    sub_fill: Optional[tuple[int, int, int]] = None,
    max_h: Optional[float] = None,
    sub_gap: float = 0.0,
) -> None:
    """Draw a (possibly two-line) name vertically centred on ``cy``.

    ``x`` is the column's left edge for left alignment, or its right edge for
    right alignment. Long names wrap to two lines (e.g. "Krasnodar-Center").
    The whole block of lines is centred on ``cy`` so a wrapped name never bleeds
    into the next row.

    An optional ``sub_text`` (drawn in ``sub_font``/``sub_fill``, e.g. a small
    frequency cross-check or a threat subtitle) is stacked below the name. It
    wraps on spaces only (so hyphenated tokens like "F-14" stay intact) and may
    occupy more than one line. Each line's pitch is its own font size, matching
    the single-name rhythm. ``sub_gap`` adds vertical space above the sub-line(s)
    so they sit lower and breathe (the name keeps its position). When ``max_h``
    is given, the pitches are compressed uniformly if the stack would exceed it,
    so the block still fits the row.
    """
    h_anchor = "r" if align == "right" else "l"

    # (line text, face, colour, pitch) entries, top to bottom: name line(s) then
    # any sub-line(s). n_main marks where the optional sub-gap is inserted.
    stack = [(line, font, fill, font.size) for line in _wrap_to_width(font, text, max_w)]
    n_main = len(stack)
    if sub_text and sub_font is not None:
        for sub_line in _wrap_to_width(sub_font, sub_text, max_w, break_hyphens=False):
            stack.append((sub_line, sub_font, sub_fill or fill, sub_font.size))

    gap = sub_gap if len(stack) > n_main else 0.0
    # Centre the name+sub lines on cy as if there were no gap, then push the
    # sub-line(s) down by the gap: the name keeps its place and only the
    # sub-line moves down to breathe.
    base_total = sum(pitch for *_, pitch in stack)
    scale = max_h / (base_total + gap) if (max_h is not None and base_total + gap > max_h) else 1.0

    cursor = cy - (base_total * scale) / 2
    for i, (line_text, line_font, line_fill, pitch) in enumerate(stack):
        if i == n_main and gap:
            cursor += gap * scale
        step = pitch * scale
        _draw_text(draw, (x, cursor + step / 2), line_text, line_font, line_fill,
                   anchor=h_anchor + "m")
        cursor += step


# ===========================================================================
# Column model
# ===========================================================================


class Column:
    """One table column: label, x-range (relative to card left) and alignment."""

    __slots__ = ("key", "label", "x0", "x1", "align")

    def __init__(self, key: str, label: str, x0: int, x1: int, align: str = "left") -> None:
        self.key = key
        self.label = label
        self.x0 = x0
        self.x1 = x1
        self.align = align  # "left" or "right"

    def text_x(self, card_x0: int) -> float:
        """Absolute x for this column's text, accounting for alignment + padding."""
        if self.align == "right":
            return card_x0 + self.x1 - ROW_PAD_X
        return card_x0 + self.x0 + ROW_PAD_X

    def anchor(self) -> str:
        return "rm" if self.align == "right" else "lm"

    def inner_max_w(self) -> float:
        """Usable text width inside the column (both paddings removed)."""
        return self.x1 - self.x0 - 2 * ROW_PAD_X


# Column separators are drawn at each non-final column's x1. Offsets below were
# measured from the reference render (relative to each card's left edge).
WAYPOINT_COLS = [
    Column("slot", "SLOT", 0, 112),
    Column("name", "RESOLVED NAME", 112, COL_W),
]
AIRDROME_COLS = [
    Column("slot", "SLOT", 0, 132),
    Column("name", "RESOLVED NAME", 132, 397),
    Column("rwy", "RWY", 397, COL_W, align="right"),
]
RSBN_COLS = [
    Column("slot", "SLOT", 0, 132),
    Column("name", "STATION", 132, 352),
    Column("channel", "CHANNEL", 352, COL_W, align="right"),
]
RADIO_COLS = [
    Column("slot", "CH", 0, 132),
    Column("freq", "FREQ · MHZ", 132, 382),
    Column("mod", "MOD", 382, COL_W),
]
CMDS_COLS = [
    Column("param", "PARAMETER", 0, 352),
    Column("value", "VALUE", 352, COL_W, align="right"),
]
SPO15_COLS = [
    Column("threat", "THREAT", 0, 102),
    # DESCRIPTION borrows 16px from STATUS (whose chip needs only ~90px of its
    # column) so the longest threat subtitle fits on one line.
    Column("desc", "DESCRIPTION", 102, 348),
    Column("status", "STATUS", 348, COL_W, align="right"),
]

# Real-world emitters each SPO-15 threat letter maps to, drawn as a small
# subtitle under the description (keyed by Latin letter; only these have one).
# Wraps on spaces, so aircraft designations like "F-14" stay intact.
SPO15_THREAT_SUBTITLES = {
    "P": "(F-4 Launch/F-14 Lock)",
    "X": "(F-14 Scan)",
    "F": "(F-15/16/18 Lock)",
    "C": "(F-4E Scan)",
}
ADF_W = ADF_X1 - ADF_X0  # 952
ADF_COLS = [
    Column("channel", "CHANNEL", 0, 165),
    Column("inner", "INNER", 165, 561),
    Column("outer", "OUTER", 561, ADF_W),
]


# ===========================================================================
# Card scaffolding (header band, column-header strip, separators, empty state)
# ===========================================================================


def _draw_card_frame(
    draw: ImageDraw.ImageDraw,
    fonts: FontBook,
    box: tuple[int, int, int, int],
    title: str,
    subtitle: str,
    count: Optional[str] = None,
) -> int:
    """Draw the card border + dark header band; return the body's top y."""
    x0, y0, x1, y1 = box
    band_bottom = y0 + HEADER_BAND_H
    draw.rectangle([x0, y0, x1, band_bottom], fill=INK)
    # Title (all-caps, tracked) vertically centred in the band.
    title_font = fonts.get("bc_xbold", FS_SECTION_TITLE)
    title_text = title.upper()
    title_track = _track_px(0.04, FS_SECTION_TITLE)
    band_cy = y0 + HEADER_BAND_H / 2
    title_w = _draw_text(
        draw, (x0 + 22, band_cy + 1), title_text, title_font, WHITE,
        tracking=title_track, anchor="lm",
    )
    # Subtitle to the right of the title (dimmed, tracked, all-caps).
    if subtitle:
        _draw_text(
            draw, (x0 + 22 + title_w + 16, band_cy + 2), subtitle.upper(),
            fonts.get("barlow_med", FS_SECTION_SUB), SUBTITLE_ON_DARK,
            tracking=_track_px(0.18, FS_SECTION_SUB), anchor="lm",
        )
    # Optional right-side count, e.g. "3 / 3".
    if count:
        _draw_text(
            draw, (x1 - 22, band_cy + 1), count, fonts.get("jb_med", FS_SECTION_COUNT),
            COUNT_ON_DARK, anchor="rm",
        )
    # Structural border last so it sits cleanly on top of the band.
    draw.rectangle([x0, y0, x1, y1], outline=INK, width=STRUCT_BORDER)
    return band_bottom + 1


def _draw_column_header(
    draw: ImageDraw.ImageDraw, fonts: FontBook, columns: Sequence[Column],
    card_x0: int, card_x1: int, body_top: int,
) -> int:
    """Draw the light-grey column-header strip; return the first row's top y."""
    strip_bottom = body_top + COL_HEADER_H
    draw.rectangle([card_x0 + 1, body_top, card_x1 - 1, strip_bottom], fill=COL_HEADER)
    label_font = fonts.get("barlow_bold", FS_COL_LABEL)
    track = _track_px(0.18, FS_COL_LABEL)
    cy = body_top + COL_HEADER_H / 2
    for col in columns:
        _draw_text(
            draw, (col.text_x(card_x0), cy + 1), col.label, label_font, INK_2,
            tracking=track, anchor=col.anchor(),
        )
    # 1px ink divider beneath the strip.
    draw.line(
        [(card_x0, strip_bottom), (card_x1, strip_bottom)], fill=INK, width=INNER_DIVIDER
    )
    return strip_bottom + 1


def _draw_column_separators(
    draw: ImageDraw.ImageDraw, columns: Sequence[Column],
    card_x0: int, y0: float, y1: float,
) -> None:
    """Draw dotted vertical separators between columns over the body height."""
    for col in columns[:-1]:
        _dotted_vline(draw, card_x0 + col.x1, y0, y1, RULE)


def _draw_empty_state(
    draw: ImageDraw.ImageDraw, fonts: FontBook,
    card_x0: int, card_x1: int, body_top: int, card_y1: int, section_label: str,
) -> None:
    """Render the NO CONFIG empty-state pattern (hatched rules + centred text)."""
    cx = (card_x0 + card_x1) / 2
    cy = (body_top + card_y1) / 2
    inset = 56
    _dotted_hline(draw, card_x0 + inset, card_x1 - inset, cy - 52, RULE)
    _dotted_hline(draw, card_x0 + inset, card_x1 - inset, cy + 52, RULE)
    _draw_text(
        draw, (cx, cy - 8), "NO CONFIG", fonts.get("bc_xbold", FS_NOCONFIG), INK_3,
        tracking=_track_px(0.16, FS_NOCONFIG), anchor="mm",
    )
    _draw_text(
        draw, (cx, cy + 30), f"{section_label} NOT PROGRAMMED".upper(),
        fonts.get("barlow_med", FS_HINT), INK_3, tracking=_track_px(0.12, FS_HINT),
        anchor="mm",
    )


def _draw_row_separators(
    draw: ImageDraw.ImageDraw, card_x0: int, card_x1: int,
    rows_top: float, row_h: float, n_rows: int,
) -> None:
    """Draw dotted horizontal separators between consecutive rows."""
    for i in range(1, n_rows):
        y = rows_top + i * row_h
        _dotted_hline(draw, card_x0 + ROW_PAD_X, card_x1 - ROW_PAD_X, y, RULE)


# ===========================================================================
# Section renderers
# ===========================================================================

NO_VALUE = "—"


def _draw_slot_tag(
    draw: ImageDraw.ImageDraw, fonts: FontBook, text: str, x: float, cy: float,
    *, muted: bool = False,
) -> None:
    """Draw a monospace slot tag (WPT1, ARD1, CH 00, ...)."""
    _draw_text(
        draw, (x, cy), text, fonts.get("jb_med", FS_SLOT), INK_3 if muted else INK,
        anchor="lm",
    )


def _draw_nav_section(
    draw: ImageDraw.ImageDraw, fonts: FontBook, box: tuple[int, int, int, int],
    title: str, subtitle: str, columns: Sequence[Column], section_label: str,
    entries: list[dict], slot_prefix: str, slots: int = 3,
    *, has_runway: bool = False, has_channel: bool = False,
) -> None:
    """Render a navigation triplet card (Waypoints / Airdromes / RSBN).

    Always shows ``slots`` rows: configured entries first, then greyed
    placeholders. A section with zero entries shows the empty state instead.
    """
    x0, y0, x1, y1 = box
    body_top = _draw_card_frame(draw, fonts, box, title, subtitle)
    if not entries:
        _draw_empty_state(draw, fonts, x0, x1, body_top, y1, section_label)
        return

    rows_top = _draw_column_header(draw, fonts, columns, x0, x1, body_top)
    row_h = DEFAULT_ROW_H
    rows_end = rows_top + slots * row_h
    _draw_column_separators(draw, columns, x0, rows_top, rows_end)
    _draw_row_separators(draw, x0, x1, rows_top, row_h, slots)

    name_col = columns[1]
    name_font = fonts.get("barlow_semi", FS_NAME)
    for i in range(slots):
        cy = rows_top + (i + 0.5) * row_h
        if i < len(entries):
            entry = entries[i]
            slot = entry.get("num") or f"{slot_prefix}{i + 1}"
            _draw_slot_tag(draw, fonts, slot, x0 + ROW_PAD_X, cy)
            _draw_cell_name(
                draw, entry.get("name", ""), x0 + name_col.x0 + ROW_PAD_X, cy,
                name_col.inner_max_w(), name_font, INK,
            )
            if has_runway:
                rwy = entry.get("runway") or NO_VALUE
                _draw_text(
                    draw, (columns[2].text_x(x0), cy), str(rwy),
                    fonts.get("jb_med", FS_VALUE), INK, anchor="rm",
                )
            if has_channel:
                ch = entry.get("channel")
                # A right-aligned channel only needs right padding, so "Ch 40"
                # (~85px) fits the column on one line; no wrap.
                ch_text = f"Ch {ch}" if ch is not None else NO_VALUE
                _draw_text(
                    draw, (columns[2].text_x(x0), cy), ch_text,
                    fonts.get("jb_med", FS_VALUE), INK, anchor="rm",
                )
        else:
            # Greyed placeholder slot for muscle-memory consistency.
            _draw_slot_tag(draw, fonts, f"{slot_prefix}{i + 1}", x0 + ROW_PAD_X, cy, muted=True)
            _draw_text(
                draw, (x0 + name_col.x0 + ROW_PAD_X, cy), NO_VALUE, name_font, INK_3,
                anchor="lm",
            )


def _adf_freq_label(beacon: dict) -> str:
    """Format an ADF beacon's frequency for display, e.g. "450 kHz AM".

    Used both as the raw fallback (when the station name is unresolved) and as
    the small cross-check sub-line drawn beneath a resolved name.
    """
    modulation = beacon.get("modulation") or ""
    return f"{beacon.get('freq')} kHz {modulation}".strip()


def _draw_adf_section(
    draw: ImageDraw.ImageDraw, fonts: FontBook, box: tuple[int, int, int, int],
    channels: list[dict],
) -> None:
    """Render the ADF card (4 channels, inner/outer beacon pair each)."""
    x0, y0, x1, y1 = box
    configured = any(
        (ch.get("inner") and ch["inner"].get("freq"))
        or (ch.get("outer") and ch["outer"].get("freq"))
        for ch in channels
    )
    body_top = _draw_card_frame(
        draw, fonts, box, "ADF", "DIRECTION FINDER · INNER / OUTER",
    )
    if not configured:
        _draw_empty_state(draw, fonts, x0, x1, body_top, y1, "ADF")
        return

    rows_top = _draw_column_header(draw, fonts, ADF_COLS, x0, x1, body_top)
    n = len(channels)
    row_h = DEFAULT_ROW_H
    rows_end = rows_top + n * row_h
    _draw_column_separators(draw, ADF_COLS, x0, rows_top, rows_end)
    _draw_row_separators(draw, x0, x1, rows_top, row_h, n)

    name_font = fonts.get("barlow_semi", FS_NAME)
    freq_font = fonts.get("jb_med", FS_MOD)
    for i, ch in enumerate(channels):
        cy = rows_top + (i + 0.5) * row_h
        _draw_slot_tag(draw, fonts, f"ADF {ch.get('channel', i + 1)}", x0 + ROW_PAD_X, cy)
        for col_idx, key in ((1, "inner"), (2, "outer")):
            col = ADF_COLS[col_idx]
            text_x = x0 + col.x0 + ROW_PAD_X
            beacon = ch.get(key)
            if not (beacon and beacon.get("freq")):
                _draw_text(draw, (text_x, cy), NO_VALUE, name_font, INK_3, anchor="lm")
                continue
            name = beacon.get("name")
            if name:
                # Resolved station: draw the name with a small frequency
                # sub-line so the pilot can cross-check it on the in-game map.
                # max_h keeps a two-line name plus the sub-line inside the row.
                _draw_cell_name(
                    draw, name, text_x, cy, col.inner_max_w(), name_font, INK,
                    sub_text=_adf_freq_label(beacon), sub_font=freq_font,
                    sub_fill=INK_2, max_h=row_h - 10,
                )
            else:
                # Unresolved: keep the raw "450 kHz AM" frequency as the value.
                _draw_cell_name(
                    draw, _adf_freq_label(beacon), text_x, cy, col.inner_max_w(),
                    name_font, INK,
                )


def _draw_radio_section(
    draw: ImageDraw.ImageDraw, fonts: FontBook, box: tuple[int, int, int, int],
    entries: list[dict], total_slots: int = 20,
) -> None:
    """Render the Radio card (20 compact channel rows, spans rows 2-3)."""
    x0, y0, x1, y1 = box
    configured = any(e.get("freq") for e in entries)
    body_top = _draw_card_frame(draw, fonts, box, "RADIO", "UHF / VHF PRESETS")
    if not configured:
        _draw_empty_state(draw, fonts, x0, x1, body_top, y1, "RADIO")
        return

    rows_top = _draw_column_header(draw, fonts, RADIO_COLS, x0, x1, body_top)
    n = max(len(entries), total_slots)
    row_h = RADIO_ROW_H
    rows_end = rows_top + n * row_h
    _draw_column_separators(draw, RADIO_COLS, x0, rows_top, rows_end)
    _draw_row_separators(draw, x0, x1, rows_top, row_h, n)

    freq_col, mod_col = RADIO_COLS[1], RADIO_COLS[2]
    freq_font = fonts.get("jb_med", FS_VALUE)
    mod_font = fonts.get("jb_med", FS_MOD)
    for i, entry in enumerate(entries):
        cy = rows_top + (i + 0.5) * row_h
        ch = entry.get("channel", i)
        _draw_slot_tag(draw, fonts, f"CH {ch:02d}", x0 + ROW_PAD_X, cy)
        _draw_text(
            draw, (x0 + freq_col.x0 + ROW_PAD_X, cy),
            entry.get("freq_display", ""), freq_font, INK, anchor="lm",
        )
        _draw_text(
            draw, (x0 + mod_col.x0 + ROW_PAD_X, cy),
            entry.get("modulation", ""), mod_font, INK_2, anchor="lm",
        )


def _draw_cmds_section(
    draw: ImageDraw.ImageDraw, fonts: FontBook, box: tuple[int, int, int, int],
    params: list[dict],
) -> None:
    """Render the CMDS flare-program card (6 parameter/value rows)."""
    x0, y0, x1, y1 = box
    body_top = _draw_card_frame(draw, fonts, box, "CMDS", "FLARE PROGRAM")
    if not params:
        _draw_empty_state(draw, fonts, x0, x1, body_top, y1, "CMDS")
        return

    rows_top = _draw_column_header(draw, fonts, CMDS_COLS, x0, x1, body_top)
    n = len(params)
    row_h = DEFAULT_ROW_H
    rows_end = rows_top + n * row_h
    _draw_column_separators(draw, CMDS_COLS, x0, rows_top, rows_end)
    _draw_row_separators(draw, x0, x1, rows_top, row_h, n)

    param_font = fonts.get("barlow_semi", FS_NAME)
    value_font = fonts.get("jb_med", FS_VALUE)
    value_col = CMDS_COLS[1]
    for i, param in enumerate(params):
        cy = rows_top + (i + 0.5) * row_h
        _draw_text(
            draw, (x0 + ROW_PAD_X, cy), param.get("label", ""), param_font, INK,
            anchor="lm",
        )
        _draw_text(
            draw, (value_col.text_x(x0), cy), str(param.get("value", "")), value_font,
            INK, anchor="rm",
        )


def _draw_chip(
    draw: ImageDraw.ImageDraw, fonts: FontBook, right_x: float, cy: float, state: str,
) -> None:
    """Draw a SPO-15 status chip (Lock=red, On=green, Off=grey; white fill)."""
    colour = {"Lock": DATA_RED, "On": DATA_GREEN, "Off": INK_3}.get(state, INK_3)
    label = state.upper()
    font = fonts.get("bc_xbold", FS_CHIP)
    track = _track_px(0.12, FS_CHIP)
    text_w = _text_width(font, label, track)
    chip_w = max(90, int(text_w) + 36)
    chip_h = 36
    x1 = right_x
    x0 = x1 - chip_w
    y0 = cy - chip_h / 2
    y1 = cy + chip_h / 2
    draw.rectangle([x0, y0, x1, y1], fill=WHITE, outline=colour, width=2)
    _draw_text(draw, ((x0 + x1) / 2, cy + 1), label, font, colour, tracking=track, anchor="mm")


def _draw_spo15_section(
    draw: ImageDraw.ImageDraw, fonts: FontBook, box: tuple[int, int, int, int],
    entries: list[dict],
) -> None:
    """Render the SPO-15 RWR card (6 threat rows with status chips)."""
    x0, y0, x1, y1 = box
    configured = any(e.get("state", "Off") != "Off" for e in entries)
    body_top = _draw_card_frame(draw, fonts, box, "SPO-15", "RWR LAUNCH WARN")
    if not configured:
        _draw_empty_state(draw, fonts, x0, x1, body_top, y1, "SPO-15")
        return

    rows_top = _draw_column_header(draw, fonts, SPO15_COLS, x0, x1, body_top)
    n = len(entries)
    row_h = DEFAULT_ROW_H
    rows_end = rows_top + n * row_h
    _draw_column_separators(draw, SPO15_COLS, x0, rows_top, rows_end)
    _draw_row_separators(draw, x0, x1, rows_top, row_h, n)

    glyph_font = fonts.get("oswald_bold", FS_SPO_GLYPH)
    latin_font = fonts.get("jb_med", FS_SPO_LATIN)
    desc_font = fonts.get("barlow_semi", FS_DESC)
    sub_font = fonts.get("jb_med", FS_SPO_SUB)
    desc_col, status_col = SPO15_COLS[1], SPO15_COLS[2]
    for i, entry in enumerate(entries):
        cy = rows_top + (i + 0.5) * row_h
        glyph = entry.get("threat_cyrillic", "")
        latin = entry.get("threat_latin", "")
        gx = x0 + ROW_PAD_X
        glyph_w = _draw_text(draw, (gx, cy), glyph, glyph_font, INK, anchor="lm")
        _draw_text(
            draw, (gx + glyph_w + 8, cy + 1), f"({latin})", latin_font, INK_2, anchor="lm",
        )
        # Description, with an optional small subtitle naming the real emitters
        # (same mono face/colour as the ADF frequency line) for P / F / C.
        _draw_cell_name(
            draw, f"Threat type {latin}", x0 + desc_col.x0 + ROW_PAD_X, cy,
            desc_col.inner_max_w(), desc_font, INK,
            sub_text=SPO15_THREAT_SUBTITLES.get(latin), sub_font=sub_font,
            sub_fill=INK_2, max_h=row_h - 10, sub_gap=8,
        )
        _draw_chip(draw, fonts, status_col.text_x(x0) + 0, cy, entry.get("state", "Off"))


# ===========================================================================
# Header
# ===========================================================================


def _page_tag_text(aircraft_type: str) -> str:
    """Derive the page-tag label (e.g. "MIG-29A") from the DTC aircraft type.

    The DCS module reports "MiG-29 Fulcrum"; the design convention labels this
    base variant "MIG-29A". Any explicit variant letter (S/G/...) is preserved.
    """
    text = (aircraft_type or "").strip().upper()
    if not text.startswith("MIG-29"):
        return text or "MIG-29A"
    tail = text[len("MIG-29"):].strip()
    # A trailing single-letter variant like "S"/"G" (not the word "FULCRUM").
    variant = ""
    if tail and tail[0].isalpha() and tail not in ("FULCRUM",) and not tail.startswith("FULCRUM"):
        variant = tail[0]
    return f"MIG-29{variant or 'A'}"


def _draw_header(
    draw: ImageDraw.ImageDraw, fonts: FontBook, *,
    program_number: int, terrain: str, aircraft_type: str,
    generated: datetime,
) -> None:
    """Draw the full-width page header (tag, title, subtitle, meta cells)."""
    tag_top, tag_bottom = HEADER_TOP, HEADER_BOTTOM
    tag_cy = (tag_top + tag_bottom) / 2

    # --- Page tag: full-height ink parallelogram with a notch on bottom-right.
    tag_font = fonts.get("bc_xbold", FS_TAG)
    tag_text = _page_tag_text(aircraft_type)
    pad_left, pad_right, notch = 36, 22, 22
    tag_text_w = _text_width(tag_font, tag_text)
    tag_right = MARGIN + pad_left + tag_text_w + pad_right
    draw.polygon(
        [
            (MARGIN, tag_top),
            (tag_right, tag_top),
            (tag_right - notch, tag_bottom),
            (MARGIN, tag_bottom),
        ],
        fill=INK,
    )
    # Caps-only text sits low on the metric midline, so lift it slightly to
    # optically centre within the band.
    _draw_text(draw, (MARGIN + pad_left, tag_cy - 5), tag_text, tag_font, WHITE, anchor="lm")

    # --- Title + subtitle, to the right of the tag.
    title_x = tag_right + 28
    title_font = fonts.get("bc_xbold", FS_TITLE)
    title = f"DTC · PROGRAM {program_number}"
    _draw_text(draw, (title_x, tag_top + 74), title, title_font, INK, anchor="lm")
    _draw_text(
        draw, (title_x + 2, tag_top + 131),
        "Data Transfer Cartridge Configuration Summary",
        fonts.get("barlow_med", FS_PAGE_SUB), INK_2, anchor="lm",
    )

    # --- Meta cells (THEATRE, GENERATED) in a bordered box on the right.
    meta_x1 = CONTENT_RIGHT
    meta_w = 409
    meta_x0 = meta_x1 - meta_w
    meta_mid = (tag_top + tag_bottom) // 2 + 1
    draw.rectangle([meta_x0, tag_top, meta_x1, tag_bottom], outline=INK, width=INNER_DIVIDER)
    draw.line([(meta_x0, meta_mid), (meta_x1, meta_mid)], fill=INK, width=INNER_DIVIDER)

    label_font = fonts.get("barlow_bold", FS_META_LABEL)
    value_font = fonts.get("jb_med", FS_META_VALUE)
    label_track = _track_px(0.18, FS_META_LABEL)

    def meta_cell(cell_top: int, cell_bottom: int, label: str, value: str) -> None:
        lx = meta_x0 + 22
        _draw_text(draw, (lx, cell_top + 24), label, label_font, INK_2,
                   tracking=label_track, anchor="lm")
        _draw_text(draw, (lx, cell_top + 56), value, value_font, INK, anchor="lm")

    meta_cell(tag_top, meta_mid, "THEATRE", terrain or NO_VALUE)
    meta_cell(meta_mid, tag_bottom, "GENERATED", _format_generated(generated))


def _format_generated(when: datetime) -> str:
    """Format a timestamp as 'DD Mon YYYY HH:MM UTC' in UTC."""
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc)
    return when.strftime("%d %b %Y %H:%M UTC")


# ===========================================================================
# Public API
# ===========================================================================


def _as_dict(data: Union[dict, Any]) -> dict:
    """Accept either a ProcessedDTC (dataclass) or its dict form."""
    if isinstance(data, dict):
        return data
    if is_dataclass(data) and not isinstance(data, type):
        return asdict(data)
    if hasattr(data, "to_dict"):
        return data.to_dict()
    raise KneeboardRenderError(
        f"Expected a ProcessedDTC or dict, got {type(data).__name__}."
    )


def render_kneeboard(
    data: Union[dict, Any],
    output_path: Union[str, Path],
    *,
    generated: Optional[datetime] = None,
    jpeg_quality: int = 92,
) -> Path:
    """Render a processed DTC program to a 1536x2048 JPEG kneeboard.

    Args:
        data: Resolved DTC data, as a ``ProcessedDTC`` or its ``to_dict()`` form.
        output_path: Destination ``.jpg`` path (parent dirs are created).
        generated: Timestamp shown in the GENERATED meta cell; defaults to now
            (UTC). Pass a fixed value for reproducible output.
        jpeg_quality: JPEG quality (1-100); 4:4:4 subsampling keeps text crisp.

    Returns:
        The path the image was written to.

    Raises:
        KneeboardRenderError: If the data is malformed or the image cannot be
            drawn or saved.
    """
    payload = _as_dict(data)
    fonts = FontBook()
    when = generated or datetime.now(timezone.utc)

    image = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(image)

    try:
        _draw_header(
            draw, fonts,
            program_number=int(payload.get("program_number", 0) or 0),
            terrain=str(payload.get("terrain", "")),
            aircraft_type=str(payload.get("aircraft_type", "")),
            generated=when,
        )

        waypoints = payload.get("waypoints", {}) or {}
        airdromes = payload.get("airdromes", {}) or {}
        rsbn = payload.get("rsbn", {}) or {}
        adf = payload.get("adf", {}) or {}
        radio = payload.get("radio", {}) or {}
        cmds = payload.get("cmds", {}) or {}
        spo15 = payload.get("spo15", {}) or {}

        # Row 1: navigation triplet.
        _draw_nav_section(
            draw, fonts, (COL1_X0, ROW1_TOP, COL1_X1, ROW1_BOT),
            "WAYPOINTS", "WPT", WAYPOINT_COLS, "WAYPOINTS",
            waypoints.get("entries", []), "WPT",
        )
        _draw_nav_section(
            draw, fonts, (COL2_X0, ROW1_TOP, COL2_X1, ROW1_BOT),
            "AIRDROMES", "ARD", AIRDROME_COLS, "AIRDROMES",
            airdromes.get("entries", []), "ARD", has_runway=True,
        )
        _draw_nav_section(
            draw, fonts, (COL3_X0, ROW1_TOP, COL3_X1, ROW1_BOT),
            "RSBN", "NAVAID", RSBN_COLS, "RSBN",
            rsbn.get("entries", []), "RSBN", has_channel=True,
        )

        # Row 2: ADF (cols 1-2).
        _draw_adf_section(
            draw, fonts, (ADF_X0, ROW2_TOP, ADF_X1, ROW2_BOT),
            adf.get("channels", []),
        )

        # Rows 2-3, col 3: Radio (tallest section).
        _draw_radio_section(
            draw, fonts, (COL3_X0, RADIO_TOP, COL3_X1, RADIO_BOT),
            radio.get("entries", []),
        )

        # Row 3: SPO-15 (col 1) + CMDS (col 2).
        _draw_spo15_section(
            draw, fonts, (COL1_X0, ROW3_TOP, COL1_X1, ROW3_BOT),
            spo15.get("entries", []),
        )
        _draw_cmds_section(
            draw, fonts, (COL2_X0, ROW3_TOP, COL2_X1, ROW3_BOT),
            cmds.get("params", []),
        )
    except KneeboardRenderError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any drawing failure clearly
        raise KneeboardRenderError(f"Failed while drawing kneeboard: {exc}") from exc

    out = Path(output_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out, format="JPEG", quality=jpeg_quality, subsampling=0)
    except OSError as exc:
        raise KneeboardRenderError(f"Could not save kneeboard to '{out}': {exc}") from exc

    logger.info("Kneeboard generated -> %s", out)
    return out
