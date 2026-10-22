"""Headless Blender smoke test used by CI: probe starts in Blender and answers RPC.

Run: ``xvfb-run -a blender -b --python examples/blender/smoke_test.py``
Exits non-zero on failure.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QTPY_MCP_REGISTRY_DIR"] = tempfile.mkdtemp(prefix="qtpy-mcp-blender-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bpy
from _demo_ui import build_window
from Qt import QtWidgets

from qtpy_mcp import registry
from qtpy_mcp.probe import install

TITLE = "PIPE-1234 - Blender smoke"
outcome: dict[str, Any] = {}


def rpc(port: int, token: str, method: str, params: dict[str, Any] | None = None) -> Any:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
        req = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, "token": token}
        conn.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    reply = json.loads(buf)
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply["result"]


def client() -> None:
    try:
        deadline = time.monotonic() + 20
        info = None
        while time.monotonic() < deadline and info is None:
            found = [i for i in registry.list_instances() if i.pid == os.getpid()]
            info = found[0] if found else None
            time.sleep(0.1)
        assert info is not None, "probe never registered"
        details = rpc(info.port, info.token, "instance_info")
        assert TITLE in json.dumps(details), details
        target = rpc(info.port, info.token, "find_target", {"describe": "Render Queue"})
        shot = rpc(info.port, info.token, "screenshot", {"ref": target["ref"]})
        assert shot["width"] > 0, "empty screenshot"
        assert shot["image"], "empty screenshot"
        outcome["ok"] = True
    except Exception as exc:  # reported below, in the main thread
        outcome["error"] = repr(exc)


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
window = build_window(TITLE)
window.show()
assert install(name="blender") is not None, "probe failed to start"
print("blender", bpy.app.version_string, "- probe installed")

thread = threading.Thread(target=client, daemon=True)
thread.start()
end = time.monotonic() + 60
while thread.is_alive() and time.monotonic() < end:
    app.processEvents()  # the probe lives on this thread's event loop
    time.sleep(0.005)

if not outcome.get("ok"):
    print("SMOKE TEST FAILED:", outcome.get("error", "timeout"))
    sys.exit(1)
print("SMOKE TEST OK")
