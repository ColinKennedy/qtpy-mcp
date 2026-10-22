"""GIF encoding for recorded frames (Pillow). Runs in the MCP server, not in the app."""

from __future__ import annotations

import io
import itertools
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image

__all__ = ["EncodedGif", "encode_gif"]

DEFAULT_MAX_WIDTH = 640


@dataclass(frozen=True)
class EncodedGif:
    """An encoded animated GIF plus a few facts about it."""

    data: bytes
    frames: int
    duration_s: float
    width: int
    height: int


def _fit_to_canvas(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Place ``img`` on a ``size`` canvas (frames can change size mid-recording)."""
    if img.size == size:
        return img
    canvas = Image.new("RGB", size, (0, 0, 0))
    canvas.paste(img.crop((0, 0, min(img.width, size[0]), min(img.height, size[1]))))
    return canvas


def encode_gif(
    frames: Sequence[tuple[bytes, float]],
    *,
    last_frame_ms: int = 100,
    max_width: int | None = DEFAULT_MAX_WIDTH,
    min_frame_ms: int = 20,
) -> EncodedGif:
    """Encode ``(png_bytes, timestamp_seconds)`` frames into a looping GIF."""
    if not frames:
        raise ValueError("No frames to encode.")
    decoded = [Image.open(io.BytesIO(png)).convert("RGB") for png, _ in frames]
    base = decoded[0].size
    out_size = base
    if max_width and base[0] > max_width:
        out_size = (max_width, max(1, round(base[1] * max_width / base[0])))
    images: list[Image.Image] = []
    for img in decoded:
        fitted = _fit_to_canvas(img, base)
        if out_size != base:
            fitted = fitted.resize(out_size, Image.Resampling.LANCZOS)
        images.append(
            fitted.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        )

    stamps = [t for _, t in frames]
    durations = [
        max(min_frame_ms, round((b - a) * 100) * 10) for a, b in itertools.pairwise(stamps)
    ]
    durations.append(max(min_frame_ms, last_frame_ms))

    buf = io.BytesIO()
    images[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )
    return EncodedGif(
        data=buf.getvalue(),
        frames=len(images),
        duration_s=sum(durations) / 1000.0,
        width=out_size[0],
        height=out_size[1],
    )
