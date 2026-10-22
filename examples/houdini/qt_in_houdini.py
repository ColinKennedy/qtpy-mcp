"""Run in Houdini's Python Shell (or a shelf tool): show a Qt window the MCP can see.

Houdini already embeds PySide (2 or 6, depending on the version) and its own
QApplication, so nothing Qt-related needs installing. Add qtpy-mcp *without* its
dependencies (Houdini provides the binding)::

    hython -m pip install --no-deps qtpy-mcp Qt.py

Several Houdinis can run side by side: each registers under its own process id and window
title. Name the window after the ticket you are working on, then ask your MCP client
"screenshot the PIPE-1234 window's docked Render Queue".
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import hou
from Qt import QtWidgets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _demo_ui import build_window

from qtpy_mcp.probe import install

app = QtWidgets.QApplication.instance()  # Houdini's own
window = build_window(f"PIPE-1234 - Houdini {hou.applicationVersionString()} (pid {os.getpid()})")
window.setParent(hou.qt.mainWindow(), window.windowFlags())  # keep it above Houdini
window.show()
probe = install(name="houdini")
print("qtpy-mcp probe:", probe.instance_id if probe else "FAILED")
