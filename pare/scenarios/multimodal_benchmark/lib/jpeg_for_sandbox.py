"""Resize/recompress JPEG (or raster) bytes for sandbox + email attachments.

Email-related tools often stringify full attachment payloads; large images exceed LLM
context limits. Downscale only when the payload is larger than a soft byte cap.
"""

from __future__ import annotations

from io import BytesIO

_DEFAULT_MAX_SIDE_PX = 1024
_DEFAULT_JPEG_QUALITY = 82
_DEFAULT_INLINE_BYTES_SOFT_CAP = 120_000


def jpeg_bytes_for_sandbox(
    raw: bytes,
    *,
    max_side_px: int = _DEFAULT_MAX_SIDE_PX,
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
    inline_bytes_soft_cap: int = _DEFAULT_INLINE_BYTES_SOFT_CAP,
) -> bytes:
    """Return JPEG bytes suitable for Files/email state and tool traces.

    If ``raw`` is already small, it is returned unchanged (avoids extra loss).
    Otherwise the image is decoded with Pillow, optionally downscaled, and re-encoded as JPEG.
    """
    if len(raw) <= inline_bytes_soft_cap:
        return raw
    from PIL import Image

    image = Image.open(BytesIO(raw)).convert("RGB")
    w, h = image.size
    m = max(w, h)
    if m > max_side_px:
        scale = max_side_px / m
        image = image.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    out = BytesIO()
    image.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
    return out.getvalue()
