"""qtpy-mcp probe: in-process instrumentation for Qt/PySide6 applications.

Usage::

    from qtpy_mcp.probe import install
    install()  # or install(port=<custom port>)
"""

from __future__ import annotations

import atexit
import collections
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from qtpy_mcp import registry
from qtpy_mcp.probe._qt import QT_BINDING, QtCore, QtWidgets, scoped
from qtpy_mcp.probe.api_inspector import ApiInspector
from qtpy_mcp.probe.finder import SemanticFinder
from qtpy_mcp.probe.interactor import Interactor
from qtpy_mcp.probe.introspector import Introspector
from qtpy_mcp.probe.layout_inspector import LayoutInspector
from qtpy_mcp.probe.recording import RecordingManager
from qtpy_mcp.probe.ref_registry import RefRegistry
from qtpy_mcp.probe.rpc_server import JsonRpcServer
from qtpy_mcp.probe.scene_inspector import SceneInspector
from qtpy_mcp.probe.screenshotter import Screenshotter
from qtpy_mcp.probe.thread_inspector import ThreadInspector
from qtpy_mcp.probe.vtk_inspector import VtkInspector

QObject = QtCore.QObject
QThread = QtCore.QThread
QApplication = QtWidgets.QApplication

DEFAULT_PORT = 0  # 0 = pick a free port; instances are found through the registry
PROBE_OBJECT_NAME = "qtpy_mcp_probe"
MESSAGE_BUFFER_MAX = 500

_MsgType = scoped(QtCore).QtMsgType

# Map QtMsgType enum values to string level names
_MSG_TYPE_MAP = {
    _MsgType.QtDebugMsg: "debug",
    _MsgType.QtInfoMsg: "info",
    _MsgType.QtWarningMsg: "warning",
    _MsgType.QtCriticalMsg: "critical",
    _MsgType.QtFatalMsg: "critical",
}

_LEVEL_SEVERITY = {"debug": 0, "info": 1, "warning": 2, "critical": 3}


class Probe(QObject):
    """Central coordinator: JSON-RPC server dispatching to capability modules."""

    def __init__(self, parent: QObject, port: int = DEFAULT_PORT, name: str | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(PROBE_OBJECT_NAME)
        self._token = os.environ.get("QTPY_MCP_TOKEN") or secrets.token_hex(16)
        self._name = name
        self._info: registry.InstanceInfo | None = None

        self._registry = RefRegistry()
        self._introspector = Introspector(self._registry)
        self._interactor = Interactor(self._registry)
        self._screenshotter = Screenshotter(self._registry)
        self._scene_inspector = SceneInspector(self._registry)
        self._vtk_inspector = VtkInspector(self._registry)
        self._thread_inspector = ThreadInspector()
        self._layout_inspector = LayoutInspector(self._registry)
        self._api_inspector = ApiInspector(self._registry)
        self._finder = SemanticFinder(self._registry)
        self._recorder = RecordingManager(self._registry)

        # Qt message capture ring buffer
        self._messages: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=MESSAGE_BUFFER_MAX
        )
        self._previous_handler = QtCore.qInstallMessageHandler(self._message_handler)

        self._rpc = JsonRpcServer(self._dispatch, parent=self, token=self._token)
        self._port = port

    def _message_handler(self, msg_type: Any, context: Any, message: str) -> None:
        """Callback for qInstallMessageHandler — captures Qt internal warnings."""
        level = _MSG_TYPE_MAP.get(msg_type, "warning")
        self._messages.append(
            {
                "level": level,
                "message": message,
                "timestamp": time.time(),
            }
        )
        # Chain to previous handler so messages still reach stderr
        if self._previous_handler is not None:
            self._previous_handler(msg_type, context, message)

    def _get_messages(self, level: str = "info") -> dict[str, Any]:
        """Return captured Qt messages at or above the given severity level, then clear."""
        min_severity = _LEVEL_SEVERITY.get(level, 1)
        filtered = [m for m in self._messages if _LEVEL_SEVERITY.get(m["level"], 0) >= min_severity]
        self._messages.clear()
        return {"messages": filtered, "count": len(filtered)}

    def start(self) -> bool:
        if not self._rpc.start(self._port):
            return False
        self._port = self._rpc.port()
        self._register()
        return True

    def port(self) -> int:
        return self._rpc.port()

    @property
    def instance_id(self) -> str:
        return self._info.id if self._info else ""

    def _register(self) -> None:
        """Publish this probe in the registry so MCP servers can find and route to it."""
        app = QApplication.instance()
        name = _resolve_name(self._name, app)
        pid = os.getpid()
        self._info = registry.InstanceInfo(
            id=f"{name}-{pid}",
            name=name,
            pid=pid,
            port=self._port,
            token=self._token,
            binding=QT_BINDING,
            qt_version=str(QtCore.qVersion()),
            python=sys.version.split()[0],
            exe=sys.executable,
        )
        try:
            registry.register(self._info)
        except OSError:
            return  # the probe still works when addressed by port
        atexit.register(registry.unregister, self._info.id)
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    def shutdown(self) -> None:
        """Remove this instance from the registry (the socket closes with the app)."""
        if self._info is not None:
            registry.unregister(self._info.id)

    def _instance_info(self) -> dict[str, Any]:
        app = QApplication.instance()
        active = app.activeWindow() if app is not None else None
        windows = [
            w.windowTitle()
            for w in (app.topLevelWidgets() if app is not None else [])
            if w.isVisible()
        ]
        info = self._info.public() if self._info else {}
        info["windows"] = [t for t in windows if t]
        info["active_window"] = active.windowTitle() if active is not None else None
        return info

    def _dispatch(self, method: str, params: dict[str, Any]) -> object:
        if method == "ping":
            return "pong"
        if method == "instance_info":
            return self._instance_info()
        if method == "snapshot":
            return self._introspector.snapshot(**params)
        if method == "screenshot":
            return self._screenshotter.screenshot(**params)
        if method == "widget_details":
            return self._introspector.widget_details(**params)
        if method == "click":
            return self._interactor.click(**params)
        if method == "type_text":
            return self._interactor.type_text(**params)
        if method == "key_press":
            return self._interactor.key_press(**params)
        if method == "set_property":
            return self._interactor.set_property(**params)
        if method == "invoke_slot":
            return self._interactor.invoke_slot(**params)
        if method == "wait_for":
            return self._interactor.wait_for(**params)
        if method == "get_text":
            return self._interactor.get_text(**params)
        if method == "trigger_action":
            return self._interactor.trigger_action(**params)
        if method == "list_windows":
            return self._introspector.list_windows(**params)
        if method == "object_tree":
            return self._introspector.object_tree(**params)
        if method == "active_popup":
            return self._introspector.active_popup()
        if method == "menu_items":
            return self._introspector.menu_items(**params)
        if method == "scene_snapshot":
            return self._scene_inspector.scene_snapshot(**params)
        if method == "scene_item_details":
            return self._scene_inspector.scene_item_details(**params)
        if method == "vtk_scene_info":
            return self._vtk_inspector.vtk_scene_info(**params)
        if method == "vtk_screenshot":
            return self._vtk_inspector.vtk_screenshot(**params)
        if method == "qt_messages":
            return self._get_messages(**params)
        if method == "thread_check":
            return self._thread_inspector.thread_check()
        if method == "signals":
            return self._api_inspector.signals(**params)
        if method == "layout_check":
            return self._layout_inspector.layout_check()
        if method == "find_widget":
            return self._introspector.find_widget(**params)
        if method == "find_semantic":
            return self._finder.find(**params)
        if method == "find_target":
            return self._finder.find_target(**params)
        if method == "record_start":
            return self._recorder.record_start(**params)
        if method == "record_stop":
            return self._recorder.record_stop(**params)
        if method == "batch":
            return self._batch(**params)
        raise ValueError(f"Unknown method: {method}")

    def _batch(self, steps: list[Any]) -> dict[str, Any]:
        """Execute multiple RPC methods sequentially in one round trip."""
        results = []
        for i, step in enumerate(steps):
            method = step.get("method")
            params = step.get("params") or {}
            wait_ms = step.get("wait_ms", 0)

            if not method:
                results.append({"ok": False, "error": "Missing 'method' in step"})
                return {"results": results, "completed": i, "failed_at": i}

            if method == "batch":
                results.append({"ok": False, "error": "Nested batch not allowed"})
                return {"results": results, "completed": i, "failed_at": i}

            try:
                if method == "wait":
                    ms = int(params.get("ms", 0))
                    _process_events_for(ms)
                    results.append({"ok": True, "result": {"waited_ms": ms}})
                else:
                    results.append({"ok": True, "result": self._dispatch(method, params)})
            except Exception as exc:
                results.append({"ok": False, "error": str(exc)})
                return {"results": results, "completed": i, "failed_at": i}

            if wait_ms > 0:
                _process_events_for(int(wait_ms))

        return {"results": results, "completed": len(steps), "failed_at": None}


def _process_events_for(ms: int) -> None:
    """Process Qt events for the given number of milliseconds."""
    app = QApplication.instance()
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        if app:
            app.processEvents()
        remaining = deadline - time.monotonic()
        if remaining > 0:
            QThread.msleep(min(50, max(1, int(remaining * 1000))))


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.]+", "-", text).strip("-").lower() or "qt-app"


def _resolve_name(explicit: str | None, app: object) -> str:
    """Human-friendly instance name: explicit > $QTPY_MCP_NAME > app name > executable."""
    candidate = explicit or os.environ.get("QTPY_MCP_NAME") or ""
    if not candidate and app is not None:
        candidate = str(QtCore.QCoreApplication.applicationName() or "")
        if not candidate:
            candidate = Path(str(QtCore.QCoreApplication.applicationFilePath())).stem
    return _slug(candidate)


def install(port: int | None = None, name: str | None = None) -> Probe | None:
    """Attach probe to the running QApplication.

    ``port`` defaults to ``$QTPY_MCP_PORT`` or a free port (several apps can each run a
    probe; the MCP server finds them through the registry). ``name`` labels this
    instance (default: ``$QTPY_MCP_NAME``, the application name, or the executable).

    Idempotent: returns existing probe if already installed.
    Returns None if no QApplication is running or if the server fails to start.
    """
    app = QApplication.instance()
    if app is None:
        return None
    if port is None:
        port = int(os.environ.get("QTPY_MCP_PORT", str(DEFAULT_PORT)))

    for child in app.children():
        if isinstance(child, Probe) or (
            isinstance(child, QObject) and child.objectName() == PROBE_OBJECT_NAME
        ):
            return child  # type: ignore[return-value]

    probe = Probe(app, port, name)
    if not probe.start():
        probe.setParent(None)
        probe.deleteLater()
        return None
    return probe


def _auto_hook() -> None:
    """If QTPY_MCP_PROBE=1, patch QApplication to auto-install probe on creation."""
    import os as _os

    if _os.environ.get("QTPY_MCP_PROBE") != "1":
        return

    hook_flag = "_qtpy_mcp_probe_hooked"
    if getattr(QApplication, hook_flag, False):
        return
    setattr(QApplication, hook_flag, True)

    _original_init = QApplication.__init__

    def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        _original_init(self, *args, **kwargs)
        install()

    QApplication.__init__ = _patched_init  # type: ignore[method-assign]
    if QApplication.instance() is not None:
        install()


_auto_hook()
