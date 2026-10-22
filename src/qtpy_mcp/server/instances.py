"""Routing of MCP tool calls to one of possibly many running Qt apps."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from qtpy_mcp import registry
from qtpy_mcp.server.probe_client import ProbeClient, ProbeConnectionError

__all__ = ["InstanceError", "InstanceManager"]

_NONE_HELP = (
    "No running Qt app with the qtpy-mcp probe was found. Start the app with "
    "QTPY_MCP_PROBE=1, or call `qtpy_mcp.probe.install()` inside it (Houdini, Blender, "
    "Maya, ...), then try again."
)


class InstanceError(ToolError):
    """A routing problem the model/user can fix (none running, ambiguous, ...)."""


def _describe(info: registry.InstanceInfo) -> str:
    return f"{info.id} (pid {info.pid}, {info.binding or '?'} {info.qt_version})".strip()


class InstanceManager:
    """Finds live probes, remembers a default, and caches one connection per probe.

    If ``QTPY_MCP_PORT`` is set the registry is bypassed and that single probe is used
    (the pre-multi-instance behaviour); ``QTPY_MCP_TOKEN`` supplies its auth token.
    """

    def __init__(self) -> None:
        self._clients: dict[str, ProbeClient] = {}
        self._selected: str | None = None
        self._fixed: registry.InstanceInfo | None = None
        port = os.environ.get("QTPY_MCP_PORT")
        if port and int(port) > 0:
            self._fixed = registry.InstanceInfo(
                id="manual",
                name="manual",
                pid=0,
                port=int(port),
                host=os.environ.get("QTPY_MCP_HOST", "127.0.0.1"),
                token=os.environ.get("QTPY_MCP_TOKEN", ""),
            )

    async def live(self) -> list[registry.InstanceInfo]:
        if self._fixed is not None:
            return [self._fixed]
        return await asyncio.to_thread(registry.list_instances)

    async def titles(self, live: list[registry.InstanceInfo]) -> dict[str, list[str]]:
        """Visible top-level window titles per instance id (best effort, in parallel)."""

        async def one(info: registry.InstanceInfo) -> tuple[str, list[str]]:
            try:
                client = await self.client(info.id)
                data = await asyncio.wait_for(client.call("instance_info"), timeout=2)
                return info.id, [str(t) for t in data.get("windows", []) if t]
            except (InstanceError, ProbeConnectionError, TimeoutError, ValueError):
                return info.id, []

        return dict(await asyncio.gather(*(one(i) for i in live)))

    async def _listing(self, items: list[registry.InstanceInfo]) -> str:
        titles = await self.titles(items)
        lines = []
        for i in items:
            shown = "; ".join(titles.get(i.id, [])[:4])
            lines.append(f"  - {_describe(i)}" + (f" windows: {shown}" if shown else ""))
        return "\n".join(lines)

    async def match(
        self, live: list[registry.InstanceInfo], query: str | int
    ) -> list[registry.InstanceInfo]:
        """Instances a free-form reference points at.

        Accepts an id, a pid (``1252151`` or ``pid:1252151``), a name (``houdini``), an id
        prefix, or words from a window title (``PIPE-1234``, ``houdini PIPE-1234``).
        """
        q = str(query).strip()
        for prefix in ("pid:", "pid=", "pid "):
            if q.lower().startswith(prefix):
                q = q[len(prefix) :].strip()
        hits = registry.match_instance(live, q)
        if hits:
            return hits
        tokens = [t.casefold() for t in q.split() if t]
        if not tokens:
            return []
        titles = await self.titles(live)
        matched: list[registry.InstanceInfo] = []
        for info in live:
            hay = " ".join([info.id, info.name, str(info.pid), *titles.get(info.id, [])]).casefold()
            if all(t in hay for t in tokens):
                matched.append(info)
        if len(matched) > 1:  # prefer windows whose title *starts with* the reference
            starts = [
                i
                for i in matched
                if any(t.casefold().startswith(q.casefold()) for t in titles.get(i.id, []))
            ]
            if starts:
                return starts
        return matched

    async def resolve(self, instance: str | int | None = None) -> registry.InstanceInfo:
        """Pick the target app: explicit reference > selected default > the only one."""
        live = await self.live()
        if instance is not None and str(instance).strip():
            hits = await self.match(live, instance)
            if not hits:
                raise InstanceError(
                    f"No running instance matches {str(instance)!r}."
                    + (f" Running:\n{await self._listing(live)}" if live else f" {_NONE_HELP}")
                )
            if len(hits) > 1:
                raise InstanceError(
                    f"{str(instance)!r} is ambiguous; use the pid or full id:\n"
                    f"{await self._listing(hits)}"
                )
            return hits[0]
        if self._selected is not None:
            chosen = [i for i in live if i.id == self._selected]
            if chosen:
                return chosen[0]
            self._selected = None  # it exited; fall through to normal resolution
        if not live:
            raise InstanceError(_NONE_HELP)
        if len(live) > 1:
            raise InstanceError(
                f"{len(live)} Qt apps are running; say which one with the `instance` "
                f"argument (pid, id or a window title) or call qt_select_instance:\n"
                f"{await self._listing(live)}"
            )
        return live[0]

    def select(self, info: registry.InstanceInfo) -> None:
        self._selected = info.id

    @property
    def selected(self) -> str | None:
        return self._selected

    async def client(self, instance: str | int | None = None) -> ProbeClient:
        """A connected client for the resolved instance (reconnects if it dropped)."""
        info = await self.resolve(instance)
        client = self._clients.get(info.id)
        if client is None or (client.port != info.port):
            client = ProbeClient(host=info.host, port=info.port, token=info.token)
            self._clients[info.id] = client
        if not client.connected:
            try:
                await client.connect(retries=2, delay=0.3)
            except ProbeConnectionError as exc:
                self._clients.pop(info.id, None)
                raise InstanceError(f"Could not reach {info.id}: {exc}") from exc
        return client

    async def aclose(self) -> None:
        """Close every cached probe connection."""
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            await client.close()

    async def describe_all(self) -> list[dict[str, Any]]:
        """Registry data plus a live ``instance_info`` (window titles) from each probe."""
        live = await self.live()

        async def one(info: registry.InstanceInfo) -> dict[str, Any]:
            data = info.public()
            try:
                client = await self.client(info.id)
                data.update(await asyncio.wait_for(client.call("instance_info"), timeout=3))
            except (InstanceError, ProbeConnectionError, TimeoutError, ValueError) as exc:
                data["error"] = str(exc)
            data["selected"] = info.id == self._selected
            return data

        return list(await asyncio.gather(*(one(i) for i in live)))
