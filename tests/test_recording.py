"""GIF recording with real widgets: probe captures frames, server encodes the GIF.

These run on Linux, Windows and macOS CI runners alike (Qt's offscreen platform plus
Pillow), which is what makes the recorder portable: no ffmpeg, no X11 grabbing, no
platform screen-capture APIs.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from typing import Any

import pytest

from qtpy_mcp.server.gif import encode_gif
from tests.gif_util import read_gif

pytestmark = pytest.mark.qt


@pytest.fixture
def animated(qapp: Any) -> Iterator[Any]:
    from qtpy_mcp.probe._qt import QtCore, QtWidgets

    label = QtWidgets.QLabel("animated")
    label.resize(160, 90)
    label.show()
    colours = ["#e02020", "#20c020", "#2020e0"]
    state = {"i": 0}

    def step() -> None:
        state["i"] += 1
        label.setStyleSheet(f"background-color: {colours[state['i'] % 3]};")

    step()
    timer = QtCore.QTimer()
    timer.timeout.connect(step)
    timer.start(30)
    qapp.processEvents()
    yield label
    timer.stop()
    label.close()
    qapp.processEvents()


def _pump(qapp: Any, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def test_grab_png_returns_a_png_of_the_widget(qapp: Any, animated: Any) -> None:
    from qtpy_mcp.probe.recording import grab_png

    png, width, height = grab_png(animated)
    assert png.startswith(b"\x89PNG")
    assert (width, height) == (160, 90)


def test_record_a_widget_and_encode_a_real_gif(qapp: Any, animated: Any) -> None:
    from qtpy_mcp.probe.recording import RecordingManager
    from qtpy_mcp.probe.ref_registry import RefRegistry

    reg = RefRegistry()
    manager = RecordingManager(reg)
    ref = reg.register(animated, prefix="w")

    started = manager.record_start(ref=ref, fps=20, max_seconds=5)
    _pump(qapp, 0.5)
    result = manager.record_stop(started["recording_id"])

    assert result["error"] == ""
    frames = [(base64.b64decode(f["png"]), float(f["t"])) for f in result["frames"]]
    assert len(frames) >= 4
    times = [t for _, t in frames]
    assert times == sorted(times)

    encoded = encode_gif(frames, max_width=None)
    assert encoded.data[:3] == b"GIF"
    gif = read_gif(encoded.data)
    assert gif.is_animated
    assert gif.n_frames == len(frames)
    assert gif.size == (160, 90)
    assert gif.loop == 0
    assert len(set(gif.frames)) >= 2, "all frames identical: nothing was animating"


def test_record_defaults_to_the_largest_visible_window(qapp: Any, animated: Any) -> None:
    from qtpy_mcp.probe.recording import RecordingManager
    from qtpy_mcp.probe.ref_registry import RefRegistry

    manager = RecordingManager(RefRegistry())
    started = manager.record_start(fps=10, max_seconds=2)
    _pump(qapp, 0.2)
    result = manager.record_stop(started["recording_id"])
    assert result["frames"]


def test_recording_halts_itself_at_max_seconds(qapp: Any, animated: Any) -> None:
    from qtpy_mcp.probe.recording import RecordingManager
    from qtpy_mcp.probe.ref_registry import RefRegistry

    reg = RefRegistry()
    manager = RecordingManager(reg)
    started = manager.record_start(ref=reg.register(animated, prefix="w"), fps=30, max_seconds=0.3)
    _pump(qapp, 0.8)
    result = manager.record_stop(started["recording_id"])
    assert result["frames"]
    assert result["frames"][-1]["t"] <= 0.3 + 0.25  # no frames after the limit
    assert len(result["frames"]) <= 30 * 0.3 + 4


@pytest.mark.parametrize(("fps", "seconds"), [(0, 5), (1000, 5), (10, 0), (10, 100000)])
def test_record_start_validates_limits(
    qapp: Any, animated: Any, fps: float, seconds: float
) -> None:
    from qtpy_mcp.probe.recording import RecordingManager
    from qtpy_mcp.probe.ref_registry import RefRegistry

    with pytest.raises(ValueError, match="must be in"):
        RecordingManager(RefRegistry()).record_start(fps=fps, max_seconds=seconds)


def test_stopping_an_unknown_recording_is_an_error(qapp: Any) -> None:
    from qtpy_mcp.probe.recording import RecordingManager
    from qtpy_mcp.probe.ref_registry import RefRegistry

    with pytest.raises(ValueError, match="Unknown recording"):
        RecordingManager(RefRegistry()).record_stop("r999")
