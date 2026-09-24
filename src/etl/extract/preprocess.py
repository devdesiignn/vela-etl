"""OpenCV preprocessing for OCR, per DESIGN-V3.md's Extract tool inventory.

Deskew + contrast/threshold cleanup ahead of the RapidOCR adapter. General-
purpose OCR accuracy is sensitive to image quality; this step exists to
support that adapter specifically, not as a shared step every extractor runs.

normalize_orientation(), unlike the rest of this module, is a correctness
fix rather than a quality tradeoff, and both the raw-bytes and the
preprocess()'d OCR pass in rapidocr_adapter.py call it. Confirmed against
a real phone photo: cv2.imdecode()/np.frombuffer() read raw pixel data and
silently ignore EXIF orientation metadata. A photo taken in portrait but
tagged "rotate 90 degrees on display" (EXIF orientation 6 — the normal
case for a phone held upright, common on Android/iOS cameras) decodes as
sideways landscape pixels. RapidOCR then reads real, legible text but in
a scrambled reading order, since box positions are computed against the
wrong axes — this produced 0/27 successful extractions against a real
receipt sample before this fix, despite the underlying OCR being accurate.
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image as PILImage
from PIL import ImageOps, UnidentifiedImageError

from etl.extract.protocol import Image

_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def normalize_orientation(image: Image) -> Image:
    """Applies the image's own EXIF orientation tag (if any) so pixel data
    matches how the photo displays, before any decoder that ignores EXIF
    (cv2.imdecode included) touches it. Re-encodes even when no orientation
    tag is present or the image is already right-side up, since PIL's
    exif_transpose() always returns a (possibly identical) image, not the
    original bytes."""
    with PILImage.open(io.BytesIO(image)) as opened:
        corrected = ImageOps.exif_transpose(opened)
        buf = io.BytesIO()
        corrected.save(buf, format=opened.format or "PNG")
        return buf.getvalue()


def preprocess(image: Image) -> Image:
    try:
        oriented = normalize_orientation(image)
    except UnidentifiedImageError as exc:
        raise ValueError(
            "preprocess() received bytes that are not a decodable image"
        ) from exc
    array = np.frombuffer(oriented, dtype=np.uint8)
    decoded = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        raise ValueError("preprocess() received bytes that are not a decodable image")

    deskewed = _deskew(decoded)
    cleaned = _clean(deskewed)

    ok, encoded = cv2.imencode(".png", cleaned)
    if not ok:
        raise ValueError("preprocess() failed to re-encode the processed image")
    return encoded.tobytes()


def _deskew(gray: np.ndarray) -> np.ndarray:
    inverted = cv2.bitwise_not(gray)
    thresh = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]

    coords = cv2.findNonZero(thresh)
    if coords is None:  # pyright: ignore[reportUnnecessaryComparison]
        return gray

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.1:
        return gray

    height, width = gray.shape
    center = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _clean(gray: np.ndarray) -> np.ndarray:
    contrasted = _CLAHE.apply(gray)
    denoised = cv2.fastNlMeansDenoising(contrasted, h=10)
    return cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31,
        C=15,
    )
