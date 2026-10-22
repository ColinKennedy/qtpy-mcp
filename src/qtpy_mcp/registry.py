"""Per-user registry of running probes, so one MCP server can route to many apps.

Every instrumented app (Houdini, Blender, Maya, a standalone tool ...) binds a free
localhost port and drops a small JSON file here. The MCP server reads the directory,
verifies each entry is alive and routes tool calls to the instance the client picked.

Standard library only: this module is imported inside the host application.
Files are written atomically and, on POSIX, readable only by the current user because
they contain the instance's auth token.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "InstanceInfo",
    "list_instances",
    "match_instance",
    "pid_alive",
    "port_open",
    "register",
    "registry_dir",
    "unregister",
]

ENV_DIR = "QTPY_MCP_REGISTRY_DIR"


def registry_dir() -> Path:
    """Directory holding one ``<id>.json`` per live probe (created on demand)."""
    override = os.environ.get(ENV_DIR)
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(root) / "qtpy-mcp" / "instances"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "qtpy-mcp" / "instances"
    else:
        root = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("XDG_STATE_HOME")
        base = (
            Path(root) / "qtpy-mcp" / "instances"
            if root
            else (Path.home() / ".local" / "state" / "qtpy-mcp" / "instances")
        )
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class InstanceInfo:
    """Everything needed to find and talk to one running probe."""

    id: str
    name: str
    pid: int
    port: int
    token: str = ""
    host: str = "127.0.0.1"
    binding: str = ""
    qt_version: str = ""
    python: str = ""
    exe: str = ""
    started_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstanceInfo:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def public(self) -> dict[str, Any]:
        """Dict safe to show to a model/user (no auth token)."""
        d = asdict(self)
        d.pop("token", None)
        return d


def _path_for(instance_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in instance_id)
    return registry_dir() / f"{safe}.json"


def register(info: InstanceInfo) -> Path:
    """Atomically write ``info`` to the registry (owner-only permissions)."""
    path = _path_for(info.id)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(info.to_json())
        with contextlib.suppress(OSError):
            Path(tmp).chmod(0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
    return path


def unregister(instance_id: str) -> None:
    """Remove an instance's registry file (idempotent)."""
    with contextlib.suppress(OSError):
        _path_for(instance_id).unlink()


def pid_alive(pid: int) -> bool:
    """Whether a process exists. Never signals the process (unsafe on Windows)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    """Whether something accepts TCP connections on ``host:port``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def list_instances(*, prune: bool = True) -> list[InstanceInfo]:
    """Live instances, oldest first. Dead entries are deleted when ``prune`` is set."""
    found: list[InstanceInfo] = []
    for path in sorted(registry_dir().glob("*.json")):
        try:
            info = InstanceInfo.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue  # half-written or foreign file: leave it alone
        if pid_alive(info.pid) and port_open(info.host, info.port):
            found.append(info)
        elif prune:
            with contextlib.suppress(OSError):
                path.unlink()
    found.sort(key=lambda i: i.started_at)
    return found


def match_instance(instances: list[InstanceInfo], query: str) -> list[InstanceInfo]:
    """Instances matching ``query`` by exact id/name/pid, else unique-ish prefix/substring.

    Returns every candidate so callers can report ambiguity instead of guessing.
    """
    q = query.strip().casefold()
    if not q:
        return []
    exact = [i for i in instances if q in (i.id.casefold(), i.name.casefold(), str(i.pid))]
    if exact:
        return exact
    prefix = [i for i in instances if i.id.casefold().startswith(q)]
    if prefix:
        return prefix
    return [i for i in instances if q in i.id.casefold() or q in i.name.casefold()]
