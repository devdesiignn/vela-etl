"""Dump the grouped OCR lines the adapter actually parses, for one image.

The adapter runs OCR TWICE — once on the raw oriented image, once on the
preprocess()'d one — and keeps whichever parse recovers more fields. A dump
of only one pass can therefore show garbled text that the adapter never
uses. This script prints both, and marks which one the adapter would pick.

Usage: uv run python debug_dump_lines.py <path-to-image>
"""

import sys

from etl.extract.adapters.ocr_text_parser import parse
from etl.extract.adapters.rapidocr_adapter import (
    _engine,
    _group_boxes_into_lines,
    _recovered_field_count,
)
from etl.extract.preprocess import normalize_orientation, preprocess


def dump(label: str, image: bytes) -> int:
    result = _engine(image)
    if not result.txts or result.boxes is None:
        print(f"--- {label}: no text ---")
        return -1

    lines = _group_boxes_into_lines(result.boxes, result.txts)
    score = _recovered_field_count(parse("\n".join(lines)))
    print(f"--- {label} (recovered field count: {score}) ---")
    for line in lines:
        print(f"  {line!r}")
    return score


def main() -> None:
    with open(sys.argv[1], "rb") as handle:
        image = handle.read()

    raw_score = dump("RAW (orientation-normalized)", normalize_orientation(image))
    try:
        preprocessed_score = dump("PREPROCESSED", preprocess(image))
    except ValueError:
        preprocessed_score = -1
        print("--- PREPROCESSED: preprocess() failed ---")

    winner = "RAW" if raw_score >= preprocessed_score else "PREPROCESSED"
    print(f"\nThe adapter parses the {winner} pass.")


if __name__ == "__main__":
    main()
