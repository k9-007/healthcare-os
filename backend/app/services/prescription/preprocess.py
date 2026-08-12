"""Practical image prep before handwriting/OCR vision — not a research lab.

Steps: EXIF rotate → RGB → optional upscale of tiny snaps → autocontrast →
light sharpen → JPEG normalize. Deskew is best-effort and skipped when the
signal is weak so we never warp a clean scan into illegibility.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("prescription.preprocess")

MAX_SIDE = 2000
MIN_SIDE_UPSCALE = 800


def preprocess_image_bytes(data: bytes, *, filename: str = "rx.jpg") -> tuple[bytes, dict[str, Any]]:
    """Return (processed_jpeg_bytes, meta). Pass-through for non-images."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return data, {"skipped": True, "reason": "pdf", "input_bytes": len(data)}

    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError as e:
        logger.warning("Pillow missing — skipping image preprocess: %s", e)
        return data, {"skipped": True, "reason": "pillow_missing", "input_bytes": len(data)}

    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        logger.warning("unreadable image (%s) — passing original bytes through", e)
        return data, {"skipped": True, "reason": f"unreadable:{type(e).__name__}", "input_bytes": len(data)}

    meta: dict[str, Any] = {
        "input_bytes": len(data),
        "original_size": list(img.size),
        "original_mode": img.mode,
        "format": (img.format or suffix.lstrip(".") or "unknown").lower(),
    }

    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")

    w, h = img.size
    long_side = max(w, h)
    if long_side > MAX_SIDE:
        scale = MAX_SIDE / long_side
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        meta["resized"] = "down"
    elif long_side < MIN_SIDE_UPSCALE:
        scale = MIN_SIDE_UPSCALE / long_side
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        meta["resized"] = "up"
    else:
        meta["resized"] = False

    # Mild deskew — projection profile on a thumbnail; only apply small angles.
    angle = _estimate_skew_degrees(img)
    meta["deskew_degrees"] = round(angle, 2)
    if 0.4 <= abs(angle) <= 12.0:
        img = img.rotate(angle, expand=True, fillcolor=(255, 255, 255), resample=Image.Resampling.BICUBIC)
        meta["deskewed"] = True
    else:
        meta["deskewed"] = False

    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=80, threshold=3))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90, optimize=True)
    processed = out.getvalue()
    meta["output_bytes"] = len(processed)
    meta["output_size"] = list(img.size)
    return processed, meta


def preprocess_image_file(path: Path) -> tuple[Path, dict[str, Any]]:
    """Preprocess in place beside the original; returns path to feed vision."""
    data = path.read_bytes()
    processed, meta = preprocess_image_bytes(data, filename=path.name)
    if meta.get("skipped") or processed == data:
        return path, meta
    out = path.with_name(f"{path.stem}_preprocessed.jpg")
    out.write_bytes(processed)
    meta["output_path"] = str(out)
    return out, meta


def _estimate_skew_degrees(img: "Image.Image") -> float:
    """Cheap skew estimate via horizontal projection variance over angle sweep."""
    from PIL import Image

    gray = img.convert("L")
    thumb = gray.copy()
    thumb.thumbnail((400, 400), Image.Resampling.BILINEAR)
    best_angle = 0.0
    best_score = -1.0
    for angle in (-8, -6, -4, -3, -2, -1, 0, 1, 2, 3, 4, 6, 8):
        rotated = thumb.rotate(angle, expand=False, fillcolor=255)
        # Ink = dark pixels; variance of row sums peaks when lines are level.
        pixels = list(rotated.getdata())
        w, h = rotated.size
        row_sums = [0] * h
        for y in range(h):
            row = pixels[y * w : (y + 1) * w]
            row_sums[y] = sum(1 for p in row if p < 180)
        mean = sum(row_sums) / max(1, h)
        var = sum((v - mean) ** 2 for v in row_sums) / max(1, h)
        if var > best_score:
            best_score = var
            best_angle = float(angle)
    return best_angle
