"""OCR via rapidocr (pip-only, onnxruntime CPU). Lazily loaded; optional."""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)
_engine = None
_lock = threading.Lock()


def available() -> bool:
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _get():
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr import RapidOCR
            _engine = RapidOCR()
    return _engine


def image_to_text(png: bytes) -> str:
    """Run OCR and join boxes in reading order (rows by y, then x)."""
    import numpy as np
    from PIL import Image
    import io
    img = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
    eng = _get()
    with _lock:
        result = eng(img)
    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    if boxes is None or txts is None:
        # older tuple API: (list of [box, text, score], elapsed)
        raw = result[0] if isinstance(result, tuple) else result
        if not raw:
            return ""
        items = [(b, t) for b, t, _s in raw]
    else:
        items = list(zip(boxes, txts))
    if not items:
        return ""
    rows: list[list[tuple[float, float, str]]] = []
    for box, txt in items:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        cy = sum(ys) / len(ys)
        h = max(ys) - min(ys)
        placed = False
        for row in rows:
            if abs(row[0][1] - cy) < max(8.0, h * 0.6):
                row.append((min(xs), cy, txt))
                placed = True
                break
        if not placed:
            rows.append([(min(xs), cy, txt)])
    rows.sort(key=lambda r: r[0][1])
    lines = [" ".join(t for _x, _y, t in sorted(row)) for row in rows]
    return "\n".join(lines)
