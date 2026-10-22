from typing import Any

from . import QtCore as QtCore
from . import QtGui as QtGui
from . import QtNetwork as QtNetwork
from . import QtWidgets as QtWidgets

__binding__: str
__binding_version__: str
__qt_version__: str

def __getattr__(name: str) -> Any: ...
