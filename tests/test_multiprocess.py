"""Real processes: several Qt apps at once, one MCP server routing between them.

Each app is a separate Python process with its own QApplication and probe (offscreen),
standing in for a Houdini / Blender / Maya session. The MCP side is driven through the
in-memory client exactly as an agent would. This is the end-to-end proof that requests
reach the right application by window title, process id, name, or description.
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import anyio.from_thread
import pytest
from mcp import Client
from PIL import Image

from qtpy_mcp import registry
from qtpy_mcp.server import mcp_server
from qtpy_mcp.server.instances import InstanceManager
from tests.gif_util import read_gif

pytestmark = [pytest.mark.requires_socket, pytest.mark.qt]

ROOT = Path(__file__).resolve().parent.parent
STUB = ROOT / "tests" / "fixtures" / "dcc_stub.py"
STARTUP_TIMEOUT = 60.0


@dataclass
class App:
    name: str
    title: str
    color: tuple[int, int, int]
    alt: tuple[int, int, int]
    proc: subprocess.Popen[bytes] | None = None
    pid_file: Path | None = None

    @property
    def pid(self) -> int:
        """The app's own pid, as it registered. Popen.pid can be a launcher shim (Windows)."""
        assert self.pid_file is not None
        return int(self.pid_file.read_text(encoding="utf-8"))


APPS = [
    App("houdini", "PIPE-1234 - Houdini FX", (200, 30, 30), (230, 90, 90)),
    App("houdini", "DEV-1515 - Houdini FX", (30, 30, 200), (90, 90, 230)),
    App("blender", "Blender [layout.blend]", (30, 160, 30), (90, 210, 90)),
]


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _spawn(app: App, reg_dir: Path) -> None:
    env = dict(os.environ)
    env["QTPY_MCP_REGISTRY_DIR"] = str(reg_dir)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT), env.get("PYTHONPATH", "")])
    app.pid_file = reg_dir.parent / f"{app.name}-{len(app.title)}-{time.monotonic_ns()}.pid"
    app.proc = subprocess.Popen(
        [
            sys.executable,
            str(STUB),
            "--name",
            app.name,
            "--title",
            app.title,
            "--color",
            _hex(app.color),
            "--alt-color",
            _hex(app.alt),
            "--pid-file",
            str(app.pid_file),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _wait_registered(apps: list[App]) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    want: set[int] = set()
    have: set[int] = set()
    while time.monotonic() < deadline:
        want = {
            a.pid
            for a in apps
            if a.pid_file is not None
            and a.pid_file.exists()
            and a.pid_file.read_text(encoding="utf-8").strip()
        }
        have = {i.pid for i in registry.list_instances()}
        if len(want) == len(apps) and want <= have:
            return
        for a in apps:
            if a.proc is not None and a.proc.poll() is not None:
                err = a.proc.stderr.read().decode() if a.proc.stderr else ""
                pytest.fail(f"{a.title} exited early ({a.proc.returncode}):\n{err}")
        time.sleep(0.2)
    pytest.fail(
        f"apps did not register within {STARTUP_TIMEOUT}s: started {want}, registered {have}"
    )


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[App]]:
    reg_dir = tmp_path / "registry"
    monkeypatch.setenv(registry.ENV_DIR, str(reg_dir))
    monkeypatch.delenv("QTPY_MCP_PORT", raising=False)
    monkeypatch.setattr(mcp_server, "_instances", InstanceManager())
    global _portal
    apps = [App(a.name, a.title, a.color, a.alt) for a in APPS]
    portal_cm = anyio.from_thread.start_blocking_portal()
    _portal = portal_cm.__enter__()
    try:
        for a in apps:
            _spawn(a, reg_dir)
        _wait_registered(apps)
        yield apps
    finally:
        _portal.call(mcp_server._instances.aclose)
        portal_cm.__exit__(None, None, None)
        _portal = None
        for a in apps:
            if a.proc is not None and a.proc.poll() is None:
                a.proc.terminate()
        for a in apps:
            if a.proc is not None:
                try:
                    a.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    a.proc.kill()


_portal: Any = None


def _drive(tool: str, args: dict[str, Any]) -> Any:
    """Call a tool the way an agent would. All calls in a test share one event loop.

    The server keeps its probe connections open between calls, and asyncio streams belong
    to the loop that created them, so a fresh ``anyio.run`` per call would not do.
    """

    async def main() -> Any:
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(tool, args)

    assert _portal is not None
    return _portal.call(main)


def _text(result: Any) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def _png_of(result: Any) -> Image.Image:
    block = next(c for c in result.content if c.type == "image")
    return Image.open(io.BytesIO(base64.b64decode(block.data))).convert("RGB")


def _near(pixel: tuple[int, ...], expected: tuple[int, int, int], tol: int = 70) -> bool:
    return all(abs(p - e) <= tol for p, e in zip(pixel[:3], expected, strict=True))


def _is_app_colour(img: Image.Image, app: App) -> bool:
    """The dock body flips between two shades; either one identifies the process."""
    pixel = img.getpixel((img.width - 6, img.height - 6))
    assert isinstance(pixel, tuple)
    return _near(pixel, app.color) or _near(pixel, app.alt)


def test_every_app_is_listed_with_its_pid_and_title(fleet: list[App]) -> None:
    text = _text(_drive("qt_list_instances", {}))
    for app in fleet:
        assert str(app.pid) in text
        assert app.title in text


@pytest.mark.parametrize("which", [0, 1, 2])
def test_routes_by_window_title(fleet: list[App], which: int) -> None:
    target = fleet[which]
    token = target.title.split(" ")[0]  # "PIPE-1234", "DEV-1515", "Blender"
    result = _drive("qt_screenshot", {"instance": token, "describe": "Foo bar"})
    img = _png_of(result)
    assert _is_app_colour(img, target), f"{token} screenshot came from the wrong process"
    for other in fleet:
        if other is not target:
            assert not _is_app_colour(img, other)


@pytest.mark.parametrize("spelling", ["{pid}", "pid:{pid}", "pid {pid}", "houdini-{pid}"])
def test_routes_by_process_id(fleet: list[App], spelling: str) -> None:
    """ "Houdini process N's 'Foo bar' widget" - every way an agent might write the pid."""
    target = fleet[1]
    instance = spelling.format(pid=target.pid)
    img = _png_of(_drive("qt_screenshot", {"instance": instance, "describe": "Foo bar"}))
    assert _is_app_colour(img, target)
    assert not _is_app_colour(img, fleet[0])


def test_integer_pid_is_accepted(fleet: list[App]) -> None:
    target = fleet[0]
    img = _png_of(_drive("qt_screenshot", {"instance": target.pid, "describe": "Foo bar"}))
    assert _is_app_colour(img, target)


def test_two_apps_back_to_back_like_a_conversation(fleet: list[App]) -> None:
    """ "The PIPE-1234 dock ... now DEV-1515's." Same client, different QApplications."""
    first = _png_of(_drive("qt_screenshot", {"instance": "PIPE-1234", "describe": "Foo bar"}))
    second = _png_of(_drive("qt_screenshot", {"instance": "DEV-1515", "describe": "Foo bar"}))
    third = _png_of(_drive("qt_screenshot", {"instance": "PIPE-1234", "describe": "Foo bar"}))
    assert _is_app_colour(first, fleet[0])
    assert _is_app_colour(second, fleet[1])
    assert _is_app_colour(third, fleet[0])


def test_find_anywhere_names_the_owning_process_for_every_hit(fleet: list[App]) -> None:
    """With no instance given the search spans all apps and reports who owns each hit."""
    text = _text(_drive("qt_find_anywhere", {"describe": "Foo bar"}))
    for app in fleet:
        assert f"{app.name}-{app.pid}" in text


def test_ambiguous_name_is_an_error_that_lists_candidates(fleet: list[App]) -> None:
    result = _drive("qt_screenshot", {"instance": "houdini", "describe": "Foo bar"})
    assert result.is_error
    text = _text(result)
    assert str(fleet[0].pid) in text
    assert str(fleet[1].pid) in text
    assert "PIPE-1234" in text
    assert "DEV-1515" in text


def test_unknown_instance_is_an_error(fleet: list[App]) -> None:
    result = _drive("qt_screenshot", {"instance": "maya", "describe": "Foo bar"})
    assert result.is_error
    assert "maya" in _text(result)


def test_gif_recording_from_the_chosen_process(fleet: list[App]) -> None:
    target = fleet[1]
    result = _drive(
        "qt_record_gif",
        {"instance": target.pid, "describe": "Foo bar", "duration": 0.6, "fps": 10},
    )
    assert not result.is_error, _text(result)
    block = next(c for c in result.content if c.type == "image")
    assert block.mime_type == "image/gif"
    gif = read_gif(base64.b64decode(block.data))
    assert gif.is_animated
    assert gif.n_frames >= 3
    assert len(set(gif.frames)) >= 2  # the dock really changed colour while recording
    first = Image.frombytes("RGB", gif.size, gif.frames[0])
    assert _is_app_colour(first, target)


def test_start_stop_recording_survives_other_apps_being_used(fleet: list[App]) -> None:
    started = _text(_drive("qt_record_start", {"instance": "PIPE-1234", "describe": "Foo bar"}))
    rid = started.split("recording_id=")[1].split()[0].strip(",.;")
    # Meanwhile the agent works with a different app.
    other = _png_of(_drive("qt_screenshot", {"instance": "DEV-1515", "describe": "Foo bar"}))
    assert _is_app_colour(other, fleet[1])
    time.sleep(0.4)
    stopped = _drive("qt_record_stop", {"recording_id": rid})
    assert not stopped.is_error, _text(stopped)
    block = next(c for c in stopped.content if c.type == "image")
    gif = read_gif(base64.b64decode(block.data))
    assert gif.n_frames >= 2
    assert _is_app_colour(Image.frombytes("RGB", gif.size, gif.frames[0]), fleet[0])


def test_an_app_quitting_does_not_break_routing_to_the_others(fleet: list[App]) -> None:
    gone = fleet[0]
    assert gone.proc is not None
    gone.proc.terminate()
    gone.proc.wait(timeout=15)

    listing = _text(_drive("qt_list_instances", {}))
    assert str(gone.pid) not in listing
    assert str(fleet[1].pid) in listing

    survivor = _png_of(_drive("qt_screenshot", {"instance": "DEV-1515", "describe": "Foo bar"}))
    assert _is_app_colour(survivor, fleet[1])

    result = _drive("qt_screenshot", {"instance": "PIPE-1234", "describe": "Foo bar"})
    assert result.is_error


def test_an_app_started_mid_conversation_is_found(
    fleet: list[App], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _drive("qt_list_instances", {}) is not None
    late = App("maya", "ASSET-9 - Maya", (150, 60, 150), (200, 110, 200))
    try:
        _spawn(late, tmp_path / "registry")
        _wait_registered([late])
        img = _png_of(_drive("qt_screenshot", {"instance": "ASSET-9", "describe": "Foo bar"}))
        assert _is_app_colour(img, late)
    finally:
        if late.proc is not None:
            late.proc.terminate()
            late.proc.wait(timeout=10)
