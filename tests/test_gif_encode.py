"""GIF encoding is pure Pillow, so these run without any Qt binding."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from qtpy_mcp.server.gif import encode_gif
from tests.gif_util import read_gif


def _png(color: tuple[int, int, int], size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_encode_gif_produces_animated_gif() -> None:
    frames = [(_png((255, 0, 0)), 0.0), (_png((0, 255, 0)), 0.1), (_png((0, 0, 255)), 0.2)]
    result = encode_gif(frames, last_frame_ms=100)
    assert result.data[:6] == b"GIF89a"
    assert (result.width, result.height) == (64, 48)
    gif = read_gif(result.data)
    assert gif.is_animated
    assert gif.n_frames == 3
    assert gif.size == (64, 48)
    assert gif.durations == [100, 100, 100]
    assert result.duration_s == pytest.approx(0.3)


def test_encode_gif_downscales_wide_frames() -> None:
    frames = [(_png((10, 20, 30), (1280, 720)), 0.0), (_png((30, 20, 10), (1280, 720)), 0.1)]
    result = encode_gif(frames, max_width=320)
    assert (result.width, result.height) == (320, 180)


def test_encode_gif_tolerates_frames_that_change_size() -> None:
    frames = [(_png((255, 0, 0), (60, 40)), 0.0), (_png((0, 255, 0), (80, 50)), 0.1)]
    gif = read_gif(encode_gif(frames).data)
    assert gif.size == (60, 40)
    assert gif.n_frames == 2


def test_encode_gif_requires_frames() -> None:
    with pytest.raises(ValueError, match="No frames"):
        encode_gif([])
