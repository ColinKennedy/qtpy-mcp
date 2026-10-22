"""Qt access for the probe, via Qt.py.

Qt.py resolves the binding (PySide2, PySide6, PyQt5, PyQt6) and smooths over most
module-level differences (``Signal``/``Slot``, ``QAction`` living in ``QtWidgets``,
...). It does *not* normalise Qt6's scoped enums, so :func:`scoped` provides
``Qt.MouseButton.LeftButton`` style access that also works on Qt5 bindings.

Choose a binding with ``QT_PREFERRED_BINDING`` (Qt.py's own variable) before the first
import. Inside a DCC the host's binding is
used automatically.

Usage::

    from qtpy_mcp.probe._qt import QtCore, QtWidgets, QtGui, scoped
"""

from __future__ import annotations

import importlib
from typing import Any

import Qt
from Qt import QtCore, QtGui, QtNetwork, QtWidgets

__all__ = [
    "QT_BINDING",
    "Q_ARG",
    "QtCore",
    "QtGui",
    "QtNetwork",
    "QtWidgets",
    "native",
    "scoped",
]

QT_BINDING: str = str(Qt.__binding__)


def native(module: str) -> Any:
    """The selected binding's own module (``native("QtCore")``).

    Qt.py does not re-export everything (``Q_ARG`` is one example); this reaches past it.
    """
    return importlib.import_module(f"{QT_BINDING}.{module}")


# PySide2 does not provide Q_ARG; callers fall back to calling the method directly.
Q_ARG: Any = getattr(native("QtCore"), "Q_ARG", None)


class _Scope:
    """``owner.<scope>.<name>`` with a fallback to the Qt5 flat ``owner.<name>``."""

    def __init__(self, owner: Any, scope: str) -> None:
        self._owner = owner
        self._scope = scope

    def __getattr__(self, name: str) -> Any:
        scope = getattr(self._owner, self._scope, None)
        if scope is not None and hasattr(scope, name):
            return getattr(scope, name)
        return getattr(self._owner, name)


class _Scoped:
    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def __getattr__(self, scope: str) -> _Scope:
        return _Scope(self._owner, scope)


def scoped(owner: Any) -> Any:
    """Wrap a Qt class/module so ``scoped(Qt).MouseButton.LeftButton`` works everywhere.

    Qt6 bindings expose enums as ``Qt.MouseButton.LeftButton``; Qt5 bindings expose
    ``Qt.LeftButton``. The wrapper tries the scoped form and falls back to the flat one.
    """
    return _Scoped(owner)
