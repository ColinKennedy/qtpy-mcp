"""Semantic identification, screenshots and GIFs across several apps (fake probes)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from qtpy_mcp import registry
from qtpy_mcp.server import mcp_server
from qtpy_mcp.server.instances import InstanceManager
from tests.fakes import FakeProbe, call, probes, run
from tests.gif_util import read_gif


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(registry.ENV_DIR, str(tmp_path / "reg"))
    monkeypatch.delenv("QTPY_MCP_PORT", raising=False)
    monkeypatch.setattr(mcp_server, "_instances", InstanceManager())
    monkeypatch.setattr(registry, "pid_alive", lambda pid: True)


APPS = (
    ("houdini", 1252151, ("PIPE-1234 - Houdini FX",), ("Foo bar", "Render settings")),
    ("houdini", 1252999, ("DEV-1515 - Houdini FX",), ("Foo bar",)),
    ("blender", 777001, ("Blender [layout.blend]",), ("Outliner",)),
)


def _requests(probe: FakeProbe, method: str) -> list[dict[str, Any]]:
    return [r["params"] for r in probe.requests if r["method"] == method]


def _blocks(*, instance: str | int, **args: Any) -> Any:
    async def main() -> Any:
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool("qt_screenshot", {"instance": instance, **args})

    return main


def test_screenshot_of_a_docked_widget_in_the_pipe_1234_app() -> None:
    """ "Take a picture of the PIPE-1234 window's docked widget." Routes by title."""

    async def main() -> tuple[Any, list[FakeProbe]]:
        async with probes(*APPS) as running:
            result = await _blocks(
                instance="PIPE-1234", window_title="PIPE-1234", class_name="QDockWidget"
            )()
            return result, running

    result, running = run(main)
    pipe, dev, blender = running
    assert [c.type for c in result.content] == ["image", "text"]
    assert base64.b64decode(result.content[0].data).startswith(b"\x89PNG")
    text = result.content[1].text
    assert "QDockWidget" in text
    assert "fake note from houdini" in text
    assert "Other matches" in text
    sel = _requests(pipe, "find_target")[0]
    assert sel["window_title"] == "PIPE-1234"
    assert sel["class_name"] == "QDockWidget"
    assert sel["expand"] == "auto"
    assert _requests(pipe, "screenshot")[0]["ref"] == "w9"  # the resolved target, not the match
    assert not _requests(dev, "find_target")
    assert not _requests(blender, "find_target")


def test_two_apps_back_to_back_in_one_conversation() -> None:
    """ "... the PIPE-1234 dock ... and then refer to DEV-1515, a different QApplication."""

    async def main() -> list[FakeProbe]:
        async with probes(*APPS) as running:
            for ref in ("PIPE-1234", "DEV-1515", "PIPE-1234"):
                await _blocks(instance=ref, describe="Foo bar")()
            return running

    pipe, dev, _ = run(main)
    assert len(_requests(pipe, "screenshot")) == 2
    assert len(_requests(dev, "screenshot")) == 1


def test_process_id_routing_for_semantic_screenshot() -> None:
    """ "Houdini process 1252999's 'Foo bar' widget" - by pid, not title."""

    async def main() -> list[FakeProbe]:
        async with probes(*APPS) as running:
            await _blocks(instance=1252999, describe="Foo bar")()
            return running

    pipe, dev, _ = run(main)
    assert _requests(dev, "find_target")[0]["describe"] == "Foo bar"
    assert not _requests(pipe, "find_target")


@pytest.mark.parametrize("expand", ["auto", "self", "parent", "buddy", "window"])
def test_expand_and_index_are_forwarded(expand: str) -> None:
    async def main() -> list[FakeProbe]:
        async with probes(*APPS) as running:
            await _blocks(instance="blender", describe="x", expand=expand, index=2)()
            return running

    blender = run(main)[2]
    sel = _requests(blender, "find_target")[0]
    assert sel["expand"] == expand
    assert sel["index"] == 2


def test_search_fields_and_min_score_are_forwarded() -> None:
    async def main() -> list[FakeProbe]:
        async with probes(*APPS) as running:
            await _blocks(
                instance="blender",
                describe="save the profile",
                search_fields=["tooltip", "whats_this", "status_tip"],
                min_score=0.7,
            )()
            return running

    sel = _requests(run(main)[2], "find_target")[0]
    assert sel["search_fields"] == ["tooltip", "whats_this", "status_tip"]
    assert sel["min_score"] == 0.7


def test_find_widget_semantic_mode_shows_score_and_reason() -> None:
    async def main() -> str:
        async with probes(*APPS):
            text, error = await call("qt_find_widget", {"describe": "Foo", "instance": "PIPE-1234"})
            assert not error
            return text

    text = run(main)
    assert "Foo bar" in text
    assert "score 1.0" in text
    assert "text:" in text


def test_find_widget_without_semantic_args_keeps_the_original_rpc() -> None:
    async def main() -> list[FakeProbe]:
        async with probes(*APPS) as running:
            await call("qt_find_widget", {"text": "Foo", "instance": "PIPE-1234"})
            return running

    pipe = run(main)[0]
    assert [r["method"] for r in pipe.requests if r["method"].startswith("find")] == ["find_widget"]


def test_find_anywhere_searches_every_app_and_names_the_owner() -> None:
    async def main() -> str:
        async with probes(*APPS):
            text, error = await call("qt_find_anywhere", {"describe": "Foo bar"})
            assert not error
            return text

    text = run(main)
    assert "houdini-1252151: 1 match(es)" in text
    assert "houdini-1252999: 1 match(es)" in text
    assert "blender-777001" not in text


def test_find_anywhere_with_no_hits() -> None:
    async def main() -> str:
        async with probes(*APPS):
            return (await call("qt_find_anywhere", {"describe": "nothing like this"}))[0]

    assert "No widgets matched in any of 3 running app(s)" in run(main)


# ------------------------------------------------------------------------ GIFs


def test_record_gif_produces_a_real_animated_gif_from_the_right_app() -> None:
    async def main() -> tuple[Any, list[FakeProbe]]:
        async with probes(*APPS) as running, Client(mcp_server.mcp) as client:
            result = await client.call_tool(
                "qt_record_gif",
                {"duration": 0.1, "fps": 10, "describe": "Foo bar", "instance": "DEV-1515"},
            )
            return result, running

    result, running = run(main)
    pipe, dev, _ = running
    assert result.content[0].type == "image"
    assert result.content[0].mime_type == "image/gif"
    gif = read_gif(base64.b64decode(result.content[0].data))
    assert gif.is_animated
    assert gif.n_frames == 3
    assert gif.size == (48, 32)
    assert len(set(gif.frames)) == 3  # red, green, blue frames really differ
    assert "3 frame(s)" in result.content[1].text
    assert [r["method"] for r in dev.requests if r["method"].startswith("record")] == [
        "record_start",
        "record_stop",
    ]
    assert not [r for r in pipe.requests if r["method"].startswith("record")]


def test_record_start_and_stop_route_by_the_recording_id_alone() -> None:
    async def main() -> tuple[str, Any, list[FakeProbe]]:
        async with probes(*APPS) as running:
            started, _ = await call(
                "qt_record_start", {"describe": "Foo bar", "instance": "PIPE-1234"}
            )
            rid = started.split("recording_id=")[1].splitlines()[0]
            async with Client(mcp_server.mcp) as client:
                stopped = await client.call_tool("qt_record_stop", {"recording_id": rid})
            return rid, stopped, running

    rid, stopped, running = run(main)
    pipe, dev, _ = running
    assert rid.startswith("houdini-1252151:")
    assert read_gif(base64.b64decode(stopped.content[0].data)).n_frames == 3
    assert [r["method"] for r in pipe.requests if r["method"] == "record_stop"] == ["record_stop"]
    assert not [r for r in dev.requests if r["method"].startswith("record")]


def test_record_stop_rejects_a_malformed_id() -> None:
    async def main() -> tuple[str, bool]:
        async with probes(*APPS):
            return await call("qt_record_stop", {"recording_id": "nonsense"})

    text, is_error = run(main)
    assert is_error
    assert "<instance>:<id>" in text


def test_record_gif_rejects_absurd_durations() -> None:
    async def main() -> tuple[str, bool]:
        async with probes(*APPS):
            return await call("qt_record_gif", {"duration": 500, "instance": "blender"})

    text, is_error = run(main)
    assert is_error
    assert "duration" in text


def test_gif_json_meta_and_size_are_sane() -> None:
    async def main() -> Any:
        async with probes(*APPS), Client(mcp_server.mcp) as client:
            return await client.call_tool(
                "qt_record_gif", {"duration": 0.05, "instance": "blender"}
            )

    result = run(main)
    raw = base64.b64decode(result.content[0].data)
    assert raw[:6] in (b"GIF89a", b"GIF87a")
    assert len(raw) < 100_000
