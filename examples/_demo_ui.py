"""The widgets used by the Blender and Houdini examples (plain Qt.py, no host imports)."""

from __future__ import annotations

from typing import Any

from Qt import QtCore, QtWidgets


def build_window(title: str) -> Any:
    """A main window with a form and a docked 'Render Queue' panel."""
    window = QtWidgets.QMainWindow()
    window.setWindowTitle(title)
    window.resize(560, 360)

    body = QtWidgets.QWidget()
    form = QtWidgets.QFormLayout(body)
    label = QtWidgets.QLabel("Name goes here:")
    field = QtWidgets.QLineEdit()
    field.setObjectName("assetNameField")
    field.setPlaceholderText("e.g. hero_rig")
    label.setBuddy(field)
    form.addRow(label, field)

    publish = QtWidgets.QPushButton("Publish")
    publish.setObjectName("publishButton")
    publish.setToolTip("Publish the selected asset to the shared library")
    publish.setStatusTip("Push to production")
    form.addRow(publish)
    window.setCentralWidget(body)

    dock = QtWidgets.QDockWidget("Render Queue", window)
    dock.setObjectName("renderQueueDock")
    queue = QtWidgets.QListWidget()
    queue.addItems(["shot_010 - waiting", "shot_020 - rendering", "shot_030 - done"])
    dock.setWidget(queue)
    right = (
        getattr(QtCore.Qt, "RightDockWidgetArea", None)
        or QtCore.Qt.DockWidgetArea.RightDockWidgetArea
    )
    window.addDockWidget(right, dock)
    return window
