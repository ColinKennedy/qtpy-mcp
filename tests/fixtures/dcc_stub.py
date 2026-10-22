"""A stand-in for a DCC: its own process, QApplication, titled window and docked widget.

Run as a script by ``tests/test_multiprocess.py``. Needs a Qt binding (Qt.py), nothing
else. The dock's background colour identifies which process a screenshot came from, and a
timer keeps changing it so that GIF recordings have distinct frames.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from Qt import QtCore, QtWidgets

from qtpy_mcp.probe import install
from qtpy_mcp.probe._qt import scoped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--dock", default="Foo bar")
    parser.add_argument("--color", required=True, help="dock background, #rrggbb")
    parser.add_argument("--alt-color", required=True)
    parser.add_argument("--pid-file", required=True, help="written with this process's real pid")
    args = parser.parse_args()

    app = QtWidgets.QApplication([*sys.argv[:1], "-platform", "offscreen"])
    app.setApplicationName(args.name)

    window = QtWidgets.QMainWindow()
    window.setWindowTitle(args.title)
    window.resize(480, 320)
    central = QtWidgets.QLabel("central")
    window.setCentralWidget(central)

    dock = QtWidgets.QDockWidget(args.dock, window)
    dock.setObjectName("fooDock")
    body = QtWidgets.QLabel("")
    body.setMinimumSize(160, 100)
    body.setToolTip(f"Panel owned by {args.name}")
    dock.setWidget(body)
    window.addDockWidget(scoped(QtCore.Qt).DockWidgetArea.RightDockWidgetArea, dock)
    window.show()

    state = {"flip": False}

    def paint() -> None:
        state["flip"] = not state["flip"]
        colour = args.alt_color if state["flip"] else args.color
        body.setStyleSheet(f"background-color: {colour};")

    paint()
    timer = QtCore.QTimer()
    timer.timeout.connect(paint)
    timer.start(40)

    # On Windows a venv python.exe is a launcher, so the parent's Popen.pid is not ours.
    Path(args.pid_file).write_text(str(os.getpid()), encoding="utf-8")
    install(name=args.name)
    return int(app.exec() if hasattr(app, "exec") else app.exec_())


if __name__ == "__main__":
    raise SystemExit(main())
