import cv2
import numpy as np
import pytest

from etl.extract.preprocess import preprocess


def _encode_receipt_like_image(angle_degrees: float = 0.0) -> bytes:
    img = np.full((300, 500, 3), 255, dtype=np.uint8)
    cv2.putText(
        img, "SUPREME PHARMACY", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2
    )
    cv2.putText(
        img, "TOTAL 300.00", (20, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2
    )

    if angle_degrees:
        center = (250, 150)
        matrix = cv2.getRotationMatrix2D(center, angle_degrees, 1.0)
        img = cv2.warpAffine(img, matrix, (500, 300), borderValue=(255, 255, 255))

    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_preprocess_returns_decodable_image():
    image = _encode_receipt_like_image()
    out = preprocess(image)

    array = np.frombuffer(out, dtype=np.uint8)
    decoded = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    assert decoded is not None
    assert decoded.shape[0] > 0 and decoded.shape[1] > 0


def test_preprocess_deskews_rotated_image():
    image = _encode_receipt_like_image(angle_degrees=8.0)
    out = preprocess(image)

    array = np.frombuffer(out, dtype=np.uint8)
    decoded = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    assert decoded is not None


def test_preprocess_output_is_binarized():
    image = _encode_receipt_like_image()
    out = preprocess(image)

    array = np.frombuffer(out, dtype=np.uint8)
    decoded = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    assert decoded is not None
    unique_values: set[int] = set(np.unique(decoded).tolist())
    assert unique_values <= {0, 255}


def test_preprocess_raises_on_undecodable_bytes():
    with pytest.raises(ValueError):
        preprocess(b"not an image")
