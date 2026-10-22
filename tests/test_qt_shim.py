"""Tests for the Qt binding compatibility layer."""

from qtpy_mcp.probe._qt import QT_BINDING, QtCore, QtGui, QtNetwork, QtWidgets


def test_binding_detection():
    assert QT_BINDING in ("PySide2", "PySide6", "PyQt5", "PyQt6")


def test_core_modules_available():
    assert hasattr(QtCore, "QObject")
    assert hasattr(QtCore, "Slot")
    assert hasattr(QtWidgets, "QApplication")
    assert hasattr(QtGui, "QKeyEvent")
    assert hasattr(QtNetwork, "QTcpServer")


def test_qaction_in_qtgui():
    """Qt.py exposes QAction from QtWidgets on every binding."""
    assert hasattr(QtWidgets, "QAction")
