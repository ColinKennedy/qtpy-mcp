"""Tests for the QTPY_MCP_PROBE=1 auto-hook (subprocess-based)."""

import subprocess
import sys
import tempfile
import textwrap

import pytest

RUN_TIMEOUT_SECONDS = 15
pytestmark = pytest.mark.requires_socket


def _run_script(script: str, env_vars: dict | None = None) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTPY_MCP_REGISTRY_DIR"] = tempfile.mkdtemp(prefix="qtpy-mcp-test-")
    if env_vars:
        env.update(env_vars)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=RUN_TIMEOUT_SECONDS,
    )


def test_auto_hook_installs_probe():
    result = _run_script(
        """\
        import sys
        sys.argv = ["test", "-platform", "offscreen"]
        from qtpy_mcp.probe._qt import QtWidgets as _QtWidgets
        QApplication = _QtWidgets.QApplication
        from qtpy_mcp.probe import PROBE_OBJECT_NAME
        app = QApplication(sys.argv)
        found = any(c.objectName() == PROBE_OBJECT_NAME for c in app.children())
        print(f"probe_found={found}")
        """,
        env_vars={"QTPY_MCP_PROBE": "1", "QTPY_MCP_PORT": "0"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "probe_found=True" in result.stdout


def test_auto_hook_noop_without_env():
    result = _run_script(
        """\
        import sys
        sys.argv = ["test", "-platform", "offscreen"]
        from qtpy_mcp.probe._qt import QtWidgets as _QtWidgets
        QApplication = _QtWidgets.QApplication
        from qtpy_mcp.probe import PROBE_OBJECT_NAME
        app = QApplication(sys.argv)
        found = any(c.objectName() == PROBE_OBJECT_NAME for c in app.children())
        print(f"probe_found={found}")
        """,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "probe_found=False" in result.stdout


def test_auto_hook_custom_port():
    result = _run_script(
        """\
        import sys
        sys.argv = ["test", "-platform", "offscreen"]
        from qtpy_mcp.probe._qt import QtWidgets as _QtWidgets
        QApplication = _QtWidgets.QApplication
        from qtpy_mcp.probe import Probe
        app = QApplication(sys.argv)
        probes = [c for c in app.children() if isinstance(c, Probe)]
        if probes:
            print(f"port={probes[0].port()}")
        else:
            print("no_probe")
        """,
        env_vars={"QTPY_MCP_PROBE": "1", "QTPY_MCP_PORT": "0"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "port=" in result.stdout
    port = int(result.stdout.strip().split("=")[1])
    assert port > 0
