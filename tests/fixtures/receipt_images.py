"""Renders synthetic receipt images for the OCR adapter tests.

Lives under tests/fixtures/ rather than src/ because it is test scaffolding,
not pipeline code: nothing in etl/ imports it, and it must never ship in the
installed package.

The rendered PNGs are committed under tests/fixtures/extract/. Tests read
those committed files, they do not re-render at test time. Two reasons.
Rendering depends on whichever TrueType font the host machine happens to
have, so a re-render on a different machine produces different pixels and a
different OCR read. And the repo's data-minimization stance forbids
committing real receipt photos (see CLAUDE.md), so committed synthetic
images are the only fixtures a test can rely on.

Regenerate with:

    uv run python tests/fixtures/receipt_images.py

Regeneration is deliberate, not automatic. Re-running it can change what OCR
reads back, so the test assertions have to be rechecked afterward.

Each receipt covers one distinct layout the parser has a branch for:
clean totals, "qty x unit_price = line_total" item lines, a SUB TOTAL with
no TOTAL line (ocr_text_parser.parse()'s subtotal-as-total fallback), and a
rotated page (preprocess()'s deskew step).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FIXTURE_DIR = Path(__file__).parent / "extract"

_WIDTH = 520
_MARGIN = 24
_LINE_HEIGHT = 30
_FONT_SIZE = 20

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/Library/Fonts/Courier New.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
)

CLEAN_LINES = (
    "SUPREME PHARMACY",
    "12 Ikorodu Road Lagos",
    "Date: 2026-01-15",
    "Invoice #INV20260042",
    "",
    "Paracetamol 500mg 300.00",
    "Vitamin C Tablets 450.00",
    "Cough Syrup 1250.00",
    "",
    "SUBTOTAL 2000.00",
    "TOTAL 2000.00",
)

QTY_LINES = (
    "CORNER MART",
    "5 Allen Avenue Ikeja",
    "Date: 2026-02-17",
    "",
    "Bread 2 x 200.00 = 400.00",
    "Milk 3 x 150.00 = 450.00",
    "Sugar 4 x 125.00 = 500.00",
    "",
    "TOTAL 1350.00",
)

SUBTOTAL_ONLY_LINES = (
    "MAMA PUT KITCHEN",
    "9 Herbert Macaulay Way",
    "Date: 2026-03-07",
    "",
    "Jollof Rice 1500.00",
    "Fried Chicken 2500.00",
    "",
    "SUB TOTAL 4000.00",
)

SKEWED_LINES = (
    "BLUE GATE STORES",
    "31 Awolowo Road Ikoyi",
    "Date: 2026-04-21",
    "",
    "Detergent 850.00",
    "Toothpaste 650.00",
    "",
    "TOTAL 1500.00",
)

RECEIPTS: dict[str, tuple[tuple[str, ...], float]] = {
    "clean_single_column.png": (CLEAN_LINES, 0.0),
    "qty_unit_price_items.png": (QTY_LINES, 0.0),
    "subtotal_only.png": (SUBTOTAL_ONLY_LINES, 0.0),
    "skewed.png": (SKEWED_LINES, 7.0),
}


def _load_font() -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, _FONT_SIZE)
    raise RuntimeError(
        "no monospaced TrueType font found on this machine; add one to "
        "_FONT_CANDIDATES before regenerating the receipt fixtures"
    )


def render(lines: tuple[str, ...], rotation: float = 0.0) -> Image.Image:
    """Renders one receipt as a grayscale image. `rotation` is degrees
    counter-clockwise, used only for the deliberately skewed fixture."""
    font = _load_font()
    height = _MARGIN * 2 + _LINE_HEIGHT * len(lines)
    image = Image.new("L", (_WIDTH, height), color=255)
    draw = ImageDraw.Draw(image)

    for index, line in enumerate(lines):
        draw.text((_MARGIN, _MARGIN + index * _LINE_HEIGHT), line, fill=0, font=font)

    if rotation:
        image = image.rotate(rotation, resample=Image.Resampling.BICUBIC, fillcolor=255)
    return image


def generate_all(directory: Path = FIXTURE_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, (lines, rotation) in RECEIPTS.items():
        path = directory / name
        render(lines, rotation).save(path, format="PNG", optimize=True)
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in generate_all():
        sys.stdout.write(f"wrote {written_path}\n")
