"""Registry of running probes (pure Python, no Qt)."""

from __future__ import annotations

import os
import socket
import stat
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from qtpy_mcp import registry
from qtpy_mcp.registry import InstanceInfo


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(registry.ENV_DIR, str(tmp_path / "reg"))


@pytest.fixture
def listener() -> Iterator[socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    yield sock
    sock.close()


def _info(name: str, port: int, pid: int | None = None) -> InstanceInfo:
    pid = os.getpid() if pid is None else pid
    return InstanceInfo(id=f"{name}-{pid}", name=name, pid=pid, port=port, token="secret")


def test_registry_dir_honours_override(tmp_path: Path) -> None:
    assert registry.registry_dir() == tmp_path / "reg"
    assert (tmp_path / "reg").is_dir()


def test_register_and_list_round_trip(listener: socket.socket) -> None:
    port = listener.getsockname()[1]
    registry.register(_info("houdini", port))
    found = registry.list_instances()
    assert [i.id for i in found] == [f"houdini-{os.getpid()}"]
    assert found[0].port == port
    assert found[0].token == "secret"


def test_public_view_hides_the_token(listener: socket.socket) -> None:
    info = _info("blender", listener.getsockname()[1])
    assert "token" not in info.public()
    assert "secret" not in str(info.public())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_registry_files_are_owner_only(listener: socket.socket) -> None:
    path = registry.register(_info("maya", listener.getsockname()[1]))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_many_instances_listed_oldest_first(listener: socket.socket) -> None:
    port = listener.getsockname()[1]
    a, b, c = _info("houdini", port, 1001), _info("blender", port, 1002), _info("maya", port, 1003)
    a.started_at, b.started_at, c.started_at = 30.0, 10.0, 20.0
    for i in (a, b, c):
        i.pid = os.getpid()  # keep them "alive"; ids stay distinct
        registry.register(i)
    assert [i.name for i in registry.list_instances()] == ["blender", "maya", "houdini"]


def test_long_unreachable_entry_is_pruned(listener: socket.socket) -> None:
    port = listener.getsockname()[1]
    info = _info("gone", port)
    info.started_at = time.time() - registry.STALE_AFTER_SECONDS - 10
    registry.register(info)
    listener.close()
    assert registry.list_instances() == []
    assert list(registry.registry_dir().glob("*.json")) == []


def test_recent_unreachable_entry_is_hidden_but_kept(listener: socket.socket) -> None:
    """A busy app that is slow to accept must not be forgotten for good."""
    port = listener.getsockname()[1]
    registry.register(_info("busy", port))
    listener.close()
    assert registry.list_instances() == []
    assert len(list(registry.registry_dir().glob("*.json"))) == 1


def test_dead_pid_is_pruned(listener: socket.socket) -> None:
    registry.register(_info("zombie", listener.getsockname()[1], pid=2_000_000_000))
    assert registry.list_instances() == []
    assert list(registry.registry_dir().glob("*.json")) == []


def test_prune_can_be_disabled(listener: socket.socket) -> None:
    registry.register(_info("zombie", listener.getsockname()[1], pid=2_000_000_000))
    assert registry.list_instances(prune=False) == []
    assert len(list(registry.registry_dir().glob("*.json"))) == 1


def test_unregister_is_idempotent(listener: socket.socket) -> None:
    info = _info("houdini", listener.getsockname()[1])
    registry.register(info)
    registry.unregister(info.id)
    registry.unregister(info.id)
    assert registry.list_instances() == []


def test_corrupt_files_are_ignored_not_deleted() -> None:
    bad = registry.registry_dir() / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert registry.list_instances() == []
    assert bad.exists()


def test_pid_alive() -> None:
    assert registry.pid_alive(os.getpid())
    assert not registry.pid_alive(0)
    assert not registry.pid_alive(2_000_000_000)


def test_match_instance_rules() -> None:
    items = [
        InstanceInfo(id="houdini-100", name="houdini", pid=100, port=1),
        InstanceInfo(id="houdini-200", name="houdini", pid=200, port=2),
        InstanceInfo(id="blender-300", name="blender", pid=300, port=3),
    ]
    assert [i.pid for i in registry.match_instance(items, "blender-300")] == [300]
    assert [i.pid for i in registry.match_instance(items, "300")] == [300]
    assert [i.pid for i in registry.match_instance(items, "BLEN")] == [300]
    assert {i.pid for i in registry.match_instance(items, "houdini")} == {100, 200}  # ambiguous
    assert registry.match_instance(items, "nothing") == []
    assert registry.match_instance(items, "  ") == []
