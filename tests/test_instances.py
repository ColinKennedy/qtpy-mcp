"""Routing between several running apps, using fake probes (pure Python, no Qt)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp import Client

from qtpy_mcp import registry
from qtpy_mcp.server import mcp_server
from qtpy_mcp.server.instances import InstanceError, InstanceManager
from tests.fakes import call, probes, run


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(registry.ENV_DIR, str(tmp_path / "reg"))
    monkeypatch.delenv("QTPY_MCP_PORT", raising=False)
    monkeypatch.delenv("QTPY_MCP_TOKEN", raising=False)
    monkeypatch.setattr(mcp_server, "_instances", InstanceManager())
    # Fake probes live in this one process but claim distinct pids.
    monkeypatch.setattr(registry, "pid_alive", lambda pid: True)


# ------------------------------------------------------------------ manager


def test_single_instance_needs_no_selection() -> None:
    async def main() -> str:
        async with probes(("houdini", 111)):
            return (await InstanceManager().resolve()).id

    assert run(main) == "houdini-111"


def test_no_instances_gives_actionable_error() -> None:
    async def main() -> None:
        await InstanceManager().resolve()

    with pytest.raises(InstanceError, match="QTPY_MCP_PROBE=1"):
        run(main)


def test_several_instances_without_choice_is_an_error_not_a_guess() -> None:
    async def main() -> None:
        async with probes(("houdini", 111), ("blender", 222)):
            await InstanceManager().resolve()

    with pytest.raises(InstanceError, match="2 Qt apps are running") as exc:
        run(main)
    assert "houdini-111" in str(exc.value)
    assert "blender-222" in str(exc.value)


def test_explicit_instance_by_id_name_pid_and_prefix() -> None:
    async def main() -> list[str]:
        async with probes(("houdini", 111), ("blender", 222), ("maya", 333)):
            mgr = InstanceManager()
            return [(await mgr.resolve(q)).id for q in ("blender-222", "maya", "111", "hou")]

    assert run(main) == ["blender-222", "maya-333", "houdini-111", "houdini-111"]


def test_two_of_the_same_app_are_distinguished_by_pid() -> None:
    async def main() -> tuple[str, str]:
        async with probes(("houdini", 111), ("houdini", 222)):
            mgr = InstanceManager()
            with pytest.raises(InstanceError, match="ambiguous"):
                await mgr.resolve("houdini")
            return (await mgr.resolve("111")).id, (await mgr.resolve("houdini-222")).id

    assert run(main) == ("houdini-111", "houdini-222")


def test_selected_default_is_used_and_cleared_when_it_exits() -> None:
    async def main() -> tuple[str, str]:
        async with probes(("houdini", 111), ("blender", 222)) as (_, blender):
            mgr = InstanceManager()
            mgr.select(await mgr.resolve("blender"))
            first = (await mgr.resolve()).id
            await mgr.aclose()
            await blender.stop()  # blender exits; only houdini is left
            second = (await mgr.resolve()).id
            return first, second

    assert run(main) == ("blender-222", "houdini-111")


def test_connections_are_cached_per_instance_with_correct_token() -> None:
    async def main() -> tuple[str, str, int, int]:
        async with probes(("houdini", 111), ("blender", 222)) as (h, b):
            mgr = InstanceManager()
            ch = await mgr.client("houdini")
            cb = await mgr.client("blender")
            assert ch is await mgr.client("houdini")
            a = await ch.call("snapshot")
            c = await cb.call("snapshot")
            await mgr.aclose()
            return a["tree"], c["tree"], len(h.requests), len(b.requests)

    assert run(main) == ("TREE-OF-houdini", "TREE-OF-blender", 1, 1)


def test_wrong_token_is_rejected() -> None:
    async def main() -> None:
        async with probes(("houdini", 111)) as (h,):
            assert h.info is not None
            h.info.token = "wrong"
            registry.register(h.info)
            mgr = InstanceManager()
            try:
                await (await mgr.client("houdini")).call("snapshot")
            finally:
                await mgr.aclose()

    with pytest.raises(ValueError, match="unauthorized"):
        run(main)


# ---------------------------------------------------------------- MCP tools


def test_tool_list_instances_shows_every_app() -> None:
    async def main() -> str:
        async with probes(("houdini", 111), ("blender", 222)):
            text, error = await call("qt_list_instances")
            assert not error
            return text

    text = run(main)
    assert "2 instance(s)" in text
    assert "houdini-111" in text
    assert "blender-222" in text
    assert "blender main window" in text
    assert "token" not in text


def test_tool_list_instances_when_none_running() -> None:
    async def main() -> str:
        return (await call("qt_list_instances"))[0]

    assert "No running Qt app" in run(main)


def test_requests_are_routed_to_the_instance_argument() -> None:
    async def main() -> tuple[str, str, list[int]]:
        async with probes(("houdini", 111), ("blender", 222)) as (h, b):
            a, _ = await call("qt_snapshot", {"instance": "houdini"})
            c, _ = await call("qt_snapshot", {"instance": "blender"})
            counts = [sum(1 for r in p.requests if r["method"] == "snapshot") for p in (h, b)]
            return a, c, counts

    a, c, counts = run(main)
    assert "TREE-OF-houdini" in a
    assert "TREE-OF-blender" in c
    assert counts == [1, 1]


def test_ambiguity_surfaces_as_a_tool_error_listing_choices() -> None:
    async def main() -> tuple[str, bool]:
        async with probes(("houdini", 111), ("blender", 222)):
            return await call("qt_snapshot")

    text, is_error = run(main)
    assert is_error
    assert "houdini-111" in text
    assert "qt_select_instance" in text


def test_select_instance_then_default_routing() -> None:
    async def main() -> tuple[str, str, str]:
        async with probes(("houdini", 111), ("blender", 222)):
            sel, _ = await call("qt_select_instance", {"instance": "blender"})
            snap, _ = await call("qt_snapshot")
            listing, _ = await call("qt_list_instances")
            return sel, snap, listing

    sel, snap, listing = run(main)
    assert "Selected blender-222" in sel
    assert "TREE-OF-blender" in snap
    assert "[selected]" in listing


def test_unknown_instance_is_reported_with_running_list() -> None:
    async def main() -> tuple[str, bool]:
        async with probes(("houdini", 111)):
            return await call("qt_snapshot", {"instance": "maya"})

    text, is_error = run(main)
    assert is_error
    assert "No running instance matches" in text
    assert "houdini-111" in text


def test_every_tool_accepts_an_instance_argument() -> None:
    async def main() -> list[str]:
        async with Client(mcp_server.mcp) as client:
            tools = (await client.list_tools()).tools
            return [
                t.name
                for t in tools
                if t.name not in ("qt_list_instances", "qt_find_anywhere", "qt_record_stop")
                and "instance" not in (t.input_schema.get("properties") or {})
            ]

    assert run(main) == []


# ------------------------------------------- one client, many apps (the real use)


PIPELINE = (
    ("houdini", 1252151, ("PIPE-1234 - Houdini FX", "Foo bar")),
    ("houdini", 1252999, ("DEV-1515 - Houdini FX",)),
    ("blender", 777001, ("Blender [layout.blend]",)),
    ("maya", 888002, ("Autodesk Maya 2026: shot_010.ma",)),
)


def test_route_by_window_title_then_switch_apps_in_one_conversation() -> None:
    """ "The PIPE-1234 window ... and then DEV-1515" - same two-houdini setup, one session."""

    async def main() -> list[str]:
        async with probes(*PIPELINE):
            order = ["PIPE-1234", "DEV-1515", "PIPE-1234", "Blender", "Autodesk"]
            return [(await call("qt_snapshot", {"instance": ref}))[0] for ref in order]

    out = run(main)
    assert [o.split("TREE-OF-")[1].split()[0] for o in out] == [
        "houdini", "houdini", "houdini", "blender", "maya",
    ]  # fmt: skip


def test_the_two_houdinis_are_told_apart_by_title_not_just_name() -> None:
    async def main() -> tuple[str, str]:
        async with probes(*PIPELINE):
            mgr = InstanceManager()
            a = (await mgr.resolve("PIPE-1234")).pid
            b = (await mgr.resolve("houdini DEV-1515")).pid
            await mgr.aclose()
            return str(a), str(b)

    assert run(main) == ("1252151", "1252999")


@pytest.mark.parametrize(
    "ref", [1252151, "1252151", "pid:1252151", "pid 1252151", "houdini-1252151"]
)
def test_route_by_process_id_in_every_spelling(ref: str | int) -> None:
    async def main() -> int:
        async with probes(*PIPELINE):
            mgr = InstanceManager()
            pid = (await mgr.resolve(ref)).pid
            await mgr.aclose()
            return pid

    assert run(main) == 1252151


def test_pid_routes_the_request_to_that_process_only() -> None:
    async def main() -> tuple[list[int], str]:
        async with probes(*PIPELINE) as running:
            text, _ = await call("qt_snapshot", {"instance": 1252999})
            hit = [sum(1 for r in p.requests if r["method"] == "snapshot") for p in running]
            return hit, text

    hit, text = run(main)
    assert hit == [0, 1, 0, 0]
    assert "TREE-OF-houdini" in text


def test_ambiguous_name_error_lists_pids_and_window_titles_so_the_agent_can_retry() -> None:
    async def main() -> tuple[str, bool]:
        async with probes(*PIPELINE):
            return await call("qt_snapshot", {"instance": "houdini"})

    text, is_error = run(main)
    assert is_error
    for needle in ("1252151", "1252999", "PIPE-1234", "DEV-1515"):
        assert needle in text
    assert "blender" not in text


def test_title_reference_that_matches_nothing_lists_what_is_running() -> None:
    async def main() -> tuple[str, bool]:
        async with probes(*PIPELINE):
            return await call("qt_snapshot", {"instance": "PIPE-9999"})

    text, is_error = run(main)
    assert is_error
    assert "No running instance matches" in text
    assert "maya-888002" in text


def test_list_instances_shows_titles_for_every_app() -> None:
    async def main() -> str:
        async with probes(*PIPELINE):
            return (await call("qt_list_instances"))[0]

    text = run(main)
    assert "4 instance(s)" in text
    for needle in ("PIPE-1234", "Foo bar", "DEV-1515", "layout.blend", "shot_010.ma"):
        assert needle in text


def test_apps_that_start_and_stop_mid_conversation_are_tracked() -> None:
    async def main() -> list[str]:
        seen = []
        async with probes(("houdini", 1, ("PIPE-1234",))):
            seen.append((await call("qt_snapshot", {"instance": "PIPE-1234"}))[0])
            async with probes(("blender", 2, ("DEV-1515",))):
                seen.append((await call("qt_snapshot", {"instance": "DEV-1515"}))[0])
                seen.append((await call("qt_snapshot", {"instance": "PIPE-1234"}))[0])
            seen.append((await call("qt_snapshot", {"instance": "DEV-1515"}))[0])
        return seen

    seen = run(main)
    assert "TREE-OF-houdini" in seen[0]
    assert "TREE-OF-blender" in seen[1]
    assert "TREE-OF-houdini" in seen[2]
    assert "No running instance matches" in seen[3]
