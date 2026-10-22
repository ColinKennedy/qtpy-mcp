"""Shared pytest fixtures for qtpy-mcp tests."""

from __future__ import annotations

import os
import socket
import sys

import pytest

# Probes require a token; the raw-socket tests send this one.
TEST_TOKEN = "qtpy-mcp-test-token"
os.environ["QTPY_MCP_TOKEN"] = TEST_TOKEN

# Force offscreen before any Qt import
if "-platform" not in sys.argv:
    sys.argv += ["-platform", "offscreen"]


def _socket_supported() -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return False
    sock.close()
    return True


SOCKET_SUPPORTED = _socket_supported()


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "requires_socket: requires socket creation support")


@pytest.fixture(autouse=True)
def _skip_requires_socket(request):
    if "requires_socket" in request.keywords and not SOCKET_SUPPORTED:
        pytest.skip("Sockets are not permitted in this environment")


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication (one per test run)."""
    from qtpy_mcp.probe._qt import QtWidgets as _QtWidgets

    QApplication = _QtWidgets.QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def sample_window(qapp):
    """Create a sample app window, clean up after test."""
    from tests.fixtures.sample_app import build_sample_app

    win = build_sample_app(qapp)
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    qapp.processEvents()


@pytest.fixture
def registry():
    from qtpy_mcp.probe.ref_registry import RefRegistry

    return RefRegistry()


@pytest.fixture
def introspector(registry):
    from qtpy_mcp.probe.introspector import Introspector

    return Introspector(registry)


@pytest.fixture
def interactor(registry):
    from qtpy_mcp.probe.interactor import Interactor

    return Interactor(registry)


@pytest.fixture
def screenshotter(registry):
    from qtpy_mcp.probe.screenshotter import Screenshotter

    return Screenshotter(registry)


@pytest.fixture
def scene_inspector(registry):
    from qtpy_mcp.probe.scene_inspector import SceneInspector

    return SceneInspector(registry)
