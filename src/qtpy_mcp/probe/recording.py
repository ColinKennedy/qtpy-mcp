"""Frame capture for GIF recording, inside the target app (Qt.py + stdlib only).

The probe only *captures* PNG frames on a ``QTimer`` (so the app's event loop keeps
running normally); the MCP server process encodes them to a GIF with Pillow, so no
imaging library has to be installed inside Houdini/Blender/Maya.
"""

from __future__ import annotations

import base64
import itertools
import time
from typing import Any

from qtpy_mcp.probe._qt import QtCore, QtWidgets, scoped
from qtpy_mcp.probe.ref_registry import RefRegistry

QApplication = QtWidgets.QApplication
QWidget = QtWidgets.QWidget
QBuffer = QtCore.QBuffer
QIODevice = scoped(QtCore.QIODevice)
QTimer = QtCore.QTimer

MAX_FPS = 30.0
MAX_SECONDS = 60.0
MAX_FRAME_BYTES = 10 * 1024 * 1024  # stays well under the RPC line limit once base64'd
MAX_RECORDINGS = 8


def grab_png(widget: Any, max_width: int | None = None) -> tuple[bytes, int, int]:
    """Render a widget to PNG bytes, downscaled to ``max_width`` if wider."""
    layout = widget.layout()
    if layout is not None:
        layout.activate()
    pixmap = widget.grab()
    if pixmap.isNull() or pixmap.width() == 0 or pixmap.height() == 0:
        raise ValueError("The widget has no size yet (not shown or laid out).")
    if max_width and pixmap.width() > max_width:
        height = max(1, round(pixmap.height() * max_width / pixmap.width()))
        pixmap = pixmap.scaled(max_width, height)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not pixmap.save(buf, "PNG"):
        raise RuntimeError("Qt failed to encode a frame as PNG.")
    png = bytes(buf.data())
    buf.close()
    return png, int(pixmap.width()), int(pixmap.height())


class _Recording:
    def __init__(self, ref: str, widget: Any, fps: float, max_seconds: float, max_width: int):
        self.ref = ref
        self.widget = widget
        self.fps = fps
        self.max_seconds = max_seconds
        self.max_width = max_width
        self.frames: list[tuple[bytes, float]] = []
        self.size = (0, 0)
        self.bytes = 0
        self.error = ""
        self.truncated = False
        self.t0 = time.monotonic()
        self.timer: Any = None

    def capture(self) -> None:
        png, w, h = grab_png(self.widget, self.max_width)
        if self.bytes + len(png) > MAX_FRAME_BYTES:
            self.truncated = True
            self.halt()
            return
        if not self.frames:
            self.size = (w, h)
        self.frames.append((png, time.monotonic() - self.t0))
        self.bytes += len(png)

    def tick(self) -> None:
        if time.monotonic() - self.t0 >= self.max_seconds:
            self.halt()
            return
        try:
            self.capture()
        except Exception as exc:
            self.error = f"recording stopped early: {exc}"
            self.halt()

    def halt(self) -> None:
        if self.timer is not None:
            self.timer.stop()

    @property
    def running(self) -> bool:
        return self.timer is not None and bool(self.timer.isActive())


class RecordingManager:
    """Starts/stops background frame captures of widgets."""

    def __init__(self, registry: RefRegistry) -> None:
        self._registry = registry
        self._recordings: dict[str, _Recording] = {}
        self._ids = itertools.count(1)

    def _default_window(self) -> Any:
        app = QApplication.instance()
        if app is None:
            raise ValueError("No QApplication running")
        active = app.activeWindow()
        if active is not None:
            return active
        windows = [w for w in app.topLevelWidgets() if w.isVisible() and w.isWindow()]
        if not windows:
            raise ValueError("No visible window to record")
        return max(windows, key=lambda w: int(w.width()) * int(w.height()))

    def record_start(
        self,
        ref: str | None = None,
        fps: float = 10.0,
        max_seconds: float = 10.0,
        max_width: int = 640,
    ) -> dict[str, Any]:
        if not 0 < fps <= MAX_FPS:
            raise ValueError(f"fps must be in (0, {MAX_FPS:g}]")
        if not 0 < max_seconds <= MAX_SECONDS:
            raise ValueError(f"max_seconds must be in (0, {MAX_SECONDS:g}]")
        if len(self._recordings) >= MAX_RECORDINGS:
            raise ValueError("Too many recordings in progress; stop some with qt_record_stop.")
        if ref:
            widget = self._registry.resolve_or_raise(ref)
            if not isinstance(widget, QWidget):
                raise ValueError(f"Ref {ref} is not a QWidget")
        else:
            widget = self._default_window()
            ref = self._registry.register(widget, prefix="w")
        rec = _Recording(ref, widget, fps, max_seconds, max_width)
        rec.capture()
        rec.timer = QTimer()
        rec.timer.setInterval(max(1, int(1000 / fps)))
        rec.timer.timeout.connect(rec.tick)
        rec.timer.start()
        rid = f"r{next(self._ids)}"
        self._recordings[rid] = rec
        return {"recording_id": rid, "ref": ref, "fps": fps, "max_seconds": max_seconds}

    def record_stop(self, recording_id: str) -> dict[str, Any]:
        rec = self._recordings.pop(recording_id, None)
        if rec is None:
            raise ValueError(f"Unknown recording {recording_id!r} (already stopped?)")
        was_running = rec.running
        rec.halt()
        if was_running:
            try:
                rec.capture()
            except Exception as exc:
                rec.error = rec.error or f"final frame failed: {exc}"
        return {
            "frames": [
                {"png": base64.b64encode(png).decode("ascii"), "t": round(t, 4)}
                for png, t in rec.frames
            ],
            "width": rec.size[0],
            "height": rec.size[1],
            "fps": rec.fps,
            "truncated": rec.truncated,
            "error": rec.error,
        }
