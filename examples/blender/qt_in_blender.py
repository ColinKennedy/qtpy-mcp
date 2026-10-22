"""Run inside Blender: open a Qt window and expose it to qtpy-mcp.

Blender has no Qt of its own, so install a binding into Blender's Python first
(Scripting workspace, or a terminal using Blender's interpreter)::

    <blender-python> -m pip install Qt.py PySide6-Essentials
    <blender-python> -m pip install --no-deps qtpy-mcp   # no deps: Blender needs only Qt.py

Then run this script from Blender's Text editor, or::

    blender --python examples/blender/qt_in_blender.py

Blender owns the main loop, so Qt events are pumped from a ``bpy.app.timers`` callback.
After running it, ask your MCP client e.g.:

    "Take a screenshot of the Render Queue dock in the Blender window"
    "Record a 3 second GIF of the Blender Qt window"
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy
from Qt import QtWidgets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # for _demo_ui in a checkout

from _demo_ui import build_window

from qtpy_mcp.probe import install

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
app.setApplicationName("blender")

window = build_window(f"Blender [{bpy.data.filepath or 'untitled'}] - Asset Tools")
window.show()
probe = install(name="blender")  # registers itself so the MCP server can find this process


def _pump() -> float:
    app.processEvents()
    return 0.01  # run again in 10 ms


bpy.app.timers.register(_pump, persistent=True)
print(f"qtpy-mcp probe ready: {probe.instance_id if probe else 'FAILED to start'}")
