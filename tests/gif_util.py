"""Typed helper for inspecting GIFs in tests."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image
from PIL.GifImagePlugin import GifImageFile


@dataclass(frozen=True)
class GifInfo:
    """Facts about a decoded GIF."""

    n_frames: int
    is_animated: bool
    size: tuple[int, int]
    loop: int | None
    durations: list[int]
    frames: list[bytes]  # raw RGB pixels per frame


def read_gif(data: bytes) -> GifInfo:
    """Decode every frame of a GIF."""
    gif = Image.open(io.BytesIO(data))
    try:
        if not isinstance(gif, GifImageFile):
            raise TypeError(f"expected a GIF, got {type(gif).__name__}")
        durations: list[int] = []
        frames: list[bytes] = []
        for i in range(gif.n_frames):
            gif.seek(i)
            durations.append(int(gif.info.get("duration", 0)))
            frames.append(gif.convert("RGB").tobytes())
        return GifInfo(
            n_frames=gif.n_frames,
            is_animated=gif.is_animated,
            size=gif.size,
            loop=gif.info.get("loop"),
            durations=durations,
            frames=frames,
        )
    finally:
        gif.close()
