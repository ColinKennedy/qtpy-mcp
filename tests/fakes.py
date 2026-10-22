"""Fake probes: tiny JSON-RPC servers that behave like a Qt app's probe (no Qt needed)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import anyio
from mcp import Client
from PIL import Image

from qtpy_mcp import registry
from qtpy_mcp.registry import InstanceInfo
from qtpy_mcp.server import mcp_server


def png_b64(color: tuple[int, int, int], size: tuple[int, int] = (48, 32)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


_T = TypeVar("_T")


class FakeProbe:
    """A tiny newline-delimited JSON-RPC server that behaves like a probe."""

    def __init__(
        self,
        name: str,
        pid: int,
        titles: tuple[str, ...] = (),
        widgets: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.pid = pid
        self.widgets = list(widgets)  # descriptions this fake app "contains"
        self.titles = list(titles) or [f"{name} main window"]
        self.token = f"token-{name}"
        self.requests: list[dict[str, Any]] = []
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self.info: InstanceInfo | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.info = InstanceInfo(
            id=f"{self.name}-{self.pid}",
            name=self.name,
            pid=self.pid,
            port=port,
            token=self.token,
            binding="PySide6",
            qt_version="6.7.0",
        )
        registry.register(self.info)

    async def stop(self) -> None:
        if self.info is not None:
            registry.unregister(self.info.id)
        for writer in list(self._writers):
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()
        await asyncio.sleep(0.05)  # let per-connection handlers finish before the server closes
        if self._server is not None:
            self._server.close()
            await asyncio.wait_for(self._server.wait_closed(), timeout=5)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while line := await reader.readline():
                req = json.loads(line)
                self.requests.append(req)
                if req.get("token") != self.token:
                    resp: dict[str, Any] = {
                        "jsonrpc": "2.0",
                        "error": {"code": -32001, "message": "unauthorized"},
                        "id": req["id"],
                    }
                else:
                    resp = {"jsonrpc": "2.0", "result": self._result(req), "id": req["id"]}
                writer.write(json.dumps(resp).encode() + b"\n")
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _result(self, req: dict[str, Any]) -> Any:
        method = req["method"]
        if method == "instance_info":
            return {"windows": self.titles, "active_window": self.titles[0]}
        if method == "snapshot":
            return {"tree": f"TREE-OF-{self.name}", "widget_count": 1, "generation": 1}
        if method == "list_windows":
            return {"windows": [{"title": self.name}]}
        params = req.get("params") or {}
        if method == "find_semantic":
            wanted = str(params.get("describe", "")).casefold()
            hits = [w for w in self.widgets if wanted and wanted in w.casefold()]
            return {
                "count": len(hits),
                "widgets": [
                    {
                        "ref": f"w{i + 1}",
                        "class": "QLabel",
                        "objectName": "",
                        "text": w,
                        "geometry": {"x": 0, "y": 0, "width": 10, "height": 10},
                        "visible": True,
                        "enabled": True,
                        "score": 1.0,
                        "matched_field": "text",
                        "matched_value": w,
                    }
                    for i, w in enumerate(hits)
                ],
            }
        if method == "find_target":
            return {
                "ref": "w9",
                "target": {"ref": "w9", "class": "QDockWidget", "objectName": "dock", "text": None},
                "matched": {"ref": "w8"},
                "note": f"fake note from {self.name}",
                "alternatives": [{"ref": "w10", "class": "QDockWidget", "score": 0.7}],
            }
        if method == "screenshot":
            return {"image": png_b64((10, 20, 30)), "width": 48, "height": 32, "format": "png"}
        if method == "record_start":
            return {"recording_id": f"r{len(self.requests)}", "ref": params.get("ref", "w1")}
        if method == "record_stop":
            colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
            return {
                "frames": [{"png": png_b64(c), "t": i * 0.1} for i, c in enumerate(colors)],
                "width": 48,
                "height": 32,
                "fps": 10,
                "truncated": False,
                "error": "",
            }
        return "pong"


@asynccontextmanager
async def probes(*specs: tuple[Any, ...]) -> AsyncIterator[list[FakeProbe]]:
    """Start fake probes: ``(name, pid)`` or ``(name, pid, (title, ...))``."""
    running = [FakeProbe(*spec) for spec in specs]
    for probe in running:
        await probe.start()
    try:
        yield running
    finally:
        await mcp_server._instances.aclose()
        for probe in running:
            await probe.stop()


def run(coro: Callable[[], Awaitable[_T]]) -> _T:
    return anyio.run(coro)


async def call(tool: str, args: dict[str, Any] | None = None) -> tuple[str, bool]:
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool(tool, args or {})
        text = "".join(getattr(c, "text", "") for c in result.content)
        return text, bool(result.is_error)
