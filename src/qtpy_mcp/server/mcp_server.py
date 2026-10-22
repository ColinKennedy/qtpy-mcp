"""MCP server that exposes Qt probe capabilities as MCP tools."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, TextContent

from qtpy_mcp.server.gif import DEFAULT_MAX_WIDTH as GIF_MAX_WIDTH
from qtpy_mcp.server.gif import encode_gif
from qtpy_mcp.server.instances import InstanceManager
from qtpy_mcp.server.probe_client import ProbeClient

DEFAULT_SNAPSHOT_DEPTH = 10
DEFAULT_SCREENSHOT_MAX_WIDTH = 1920
DEFAULT_SCREENSHOT_MAX_HEIGHT = 1080
DEFAULT_SCREENSHOT_FORMAT = "png"
DEFAULT_JPEG_QUALITY = 80
DEFAULT_WAIT_TIMEOUT_MS = 5000
MAX_TEXT_PREVIEW_CHARS = 5000

mcp = MCPServer(
    "qtpy-mcp",
    instructions=(
        "Qt (PySide2/6, PyQt5/6 via Qt.py) desktop application inspection and interaction.\n"
        "- qt_find_widget: search for widgets by class/name/text — prefer this over qt_snapshot "
        "when you know what you're looking for. Returns refs immediately usable with other tools.\n"
        "- qt_snapshot: full widget tree when you need to understand overall structure or discover "
        "unknown widget hierarchies.\n"
        "- qt_batch: chain multiple operations (click, type, key_press, wait, snapshot, "
        "find_widget...) in a single round trip — use this instead of separate tool calls.\n"
        "- qt_screenshot: ONLY for visual content verification (rendered plots, images, custom "
        "drawing). Never use it to check widget state — qt_snapshot and qt_get_text are faster "
        "and cheaper for that.\n"
        "- qt_type with use_clipboard=True for multi-line text (prevents newlines from submitting "
        "each line as a command in console widgets).\n"
        "- Several Qt apps (Houdini, Blender, Maya, ...) can run at once. qt_list_instances shows "
        "them. Address any of them in the SAME conversation by passing `instance` to a tool: a pid "
        '(1252151), an id (houdini-1252151), or words from a window title ("PIPE-1234"). '
        "Switch between apps freely call to call. With exactly one app running no choice is "
        "needed; with several, an ambiguous reference is an error that lists the candidates. "
        "Refs (w1, w2 ...) belong to the instance that issued them."
    ),
)

_instances = InstanceManager()


async def _ensure_connected(instance: str | int | None = None) -> ProbeClient:
    """Connected client for the target app (see qt_list_instances / qt_select_instance)."""
    return await _instances.client(instance)


async def _target(instance: str | int | None) -> tuple[str, ProbeClient]:
    """``(instance_id, client)`` for the app a call is addressed to."""
    info = await _instances.resolve(instance)
    return info.id, await _instances.client(info.id)


def _selector_params(
    describe: str,
    window_title: str,
    class_name: str,
    object_name: str,
    search_fields: list[str] | None,
    min_score: float,
    visible_only: bool = True,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "describe": describe,
        "window_title": window_title,
        "class_name": class_name,
        "object_name": object_name,
        "min_score": min_score,
        "visible_only": visible_only,
    }
    if search_fields:
        params["search_fields"] = search_fields
    return params


def _format_widgets(result: dict[str, Any], *, header: str = "") -> str:
    widgets = result.get("widgets", [])
    if not widgets:
        return "No widgets found matching criteria."
    lines = [header or f"Found {result.get('count', len(widgets))} widget(s):"]
    for w in widgets:
        g = w.get("geometry", {})
        size = f"{g.get('width', 0)}x{g.get('height', 0)}"
        name = f' "{w["objectName"]}"' if w.get("objectName") else ""
        text_val = f' text="{w["text"]}"' if w.get("text") else ""
        flags = []
        if not w.get("visible", True):
            flags.append("hidden")
        if not w.get("enabled", True):
            flags.append("disabled")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        why = ""
        if w.get("matched_field"):
            why = f" (score {w.get('score')}, {w['matched_field']}: {w.get('matched_value', '')!r})"
        lines.append(f"  - {w['class']}{name} [ref={w['ref']}] {size}{text_val}{flag_str}{why}")
    return "\n".join(lines)


# --- Instances (several Qt apps at once) ---


@mcp.tool()
async def qt_list_instances() -> str:
    """List running Qt apps that have the qtpy-mcp probe (Houdini, Blender, Maya, ...).

    Shows each instance id, process, binding and visible window titles. Use the id with
    the `instance` argument of any tool, or with qt_select_instance.
    """
    items = await _instances.describe_all()
    if not items:
        return (
            "No running Qt app with the qtpy-mcp probe was found. Start the app with "
            "QTPY_MCP_PROBE=1 or call `qtpy_mcp.probe.install()` inside it."
        )
    lines = [f"{len(items)} instance(s):"]
    for it in items:
        mark = " [selected]" if it.get("selected") else ""
        head = (
            f"  - {it['id']}{mark}: pid {it.get('pid')}, "
            f"{it.get('binding', '?')} {it.get('qt_version', '')}"
        )
        lines.append(head.rstrip())
        if it.get("error"):
            lines.append(f"      unreachable: {it['error']}")
            continue
        if it.get("active_window"):
            lines.append(f"      active window: {it['active_window']}")
        if it.get("windows"):
            lines.append("      windows: " + "; ".join(it["windows"][:8]))
    return "\n".join(lines)


@mcp.tool()
async def qt_select_instance(instance: str | int) -> str:
    """Make one running Qt app the default target for later tool calls.

    Args:
        instance: Instance id, pid, name, or words from a window title, e.g. "PIPE-1234".
    """
    info = await _instances.resolve(instance)
    _instances.select(info)
    return f"Selected {info.id} (pid {info.pid})."


# --- Snapshot & Navigation ---


@mcp.tool()
async def qt_snapshot(
    max_depth: int = DEFAULT_SNAPSHOT_DEPTH,
    root_ref: str | None = None,
    skip_hidden: bool = False,
    instance: str | int | None = None,
) -> str:
    """Capture the full Qt widget tree as a structured accessibility-like snapshot.

    Returns a YAML-like text tree with widget types, object names, text content,
    geometry, visibility, enabled state, and interaction refs (w1, w2, ...).
    Use the refs to interact with specific widgets via other tools.
    """
    client = await _ensure_connected(instance)
    params: dict[str, Any] = {"max_depth": max_depth}
    if root_ref:
        params["root_ref"] = root_ref
    if skip_hidden:
        params["skip_hidden"] = skip_hidden
    result = await client.call("snapshot", params)
    tree = result.get("tree", "")
    count = result.get("widget_count", 0)
    gen = result.get("generation", 0)
    return f"Widgets: {count} (generation {gen})\n\n{tree}"


@mcp.tool()
async def qt_find_widget(
    pattern: str = "",
    class_name: str = "",
    object_name: str = "",
    text: str = "",
    root_ref: str | None = None,
    visible_only: bool = True,
    max_results: int = 20,
    describe: str = "",
    window_title: str = "",
    search_fields: list[str] | None = None,
    min_score: float = 0.5,
    instance: str | int | None = None,
) -> str:
    """Search the widget tree for widgets matching criteria. Prefer over qt_snapshot.

    Two modes. With `describe` and/or `window_title` it is a *semantic* search: `describe`
    is matched fuzzily (typos, punctuation and case ignored) against label/button text,
    group and dock titles, tooltip, what's-this, status tip, accessible name/description,
    objectName, placeholder, tab titles, dynamic properties, class name and style sheet;
    results are ranked with the reason each matched. Otherwise the plain substring filters
    below apply.

    Registers found widgets in the ref registry — returned refs are immediately
    usable with qt_click, qt_type, qt_get_text, etc.

    Args:
        pattern: Case-insensitive substring matched against class name, objectName,
            or text content (OR logic across fields).
        class_name: Exact class name, case-insensitive (e.g. 'QLineEdit', 'QPushButton',
            'ControlWidget'). Must match the full class name, not a substring.
        object_name: Substring match on objectName.
        text: Substring match on widget text/label content.
        root_ref: Restrict search to subtree of this widget.
        visible_only: Only return visible widgets (default True).
        max_results: Cap on results returned (default 20).
        describe: Free-text description, e.g. 'Name goes here:' or 'save the profile'.
        window_title: Fuzzy match on the top-level window title (e.g. 'PIPE-1234').
        search_fields: Limit `describe` to some of: text, title, placeholder, tooltip,
            whats_this, status_tip, accessible_name, accessible_description, object_name,
            window_title, tab_text, properties, class_name, style_sheet.
        min_score: Minimum semantic match score 0-1 (default 0.5).

    At least one of pattern, class_name, object_name, text, describe or window_title
    must be provided.
    """
    client = await _ensure_connected(instance)
    if describe or window_title or search_fields:
        sem = _selector_params(
            describe, window_title, class_name, object_name, search_fields, min_score, visible_only
        )
        sem["max_results"] = max_results
        if root_ref:
            sem["root_ref"] = root_ref
        return _format_widgets(await client.call("find_semantic", sem))
    params: dict[str, Any] = {
        "pattern": pattern,
        "class_name": class_name,
        "object_name": object_name,
        "text": text,
        "visible_only": visible_only,
        "max_results": max_results,
    }
    if root_ref:
        params["root_ref"] = root_ref
    return _format_widgets(await client.call("find_widget", params))


@mcp.tool()
async def qt_screenshot(
    ref: str | None = None,
    full_window: bool = False,
    max_width: int = DEFAULT_SCREENSHOT_MAX_WIDTH,
    max_height: int = DEFAULT_SCREENSHOT_MAX_HEIGHT,
    format: str = DEFAULT_SCREENSHOT_FORMAT,
    quality: int = DEFAULT_JPEG_QUALITY,
    describe: str = "",
    window_title: str = "",
    class_name: str = "",
    object_name: str = "",
    search_fields: list[str] | None = None,
    expand: str = "auto",
    index: int = 0,
    min_score: float = 0.5,
    instance: str | int | None = None,
) -> list[Any]:
    """Take a screenshot of a window or any widget, identified by ref OR by description.

    Identify the widget semantically instead of by ref, e.g. describe="Name goes here:"
    (a label: captures the widget that holds it), describe="save the profile" (tooltip),
    window_title="PIPE-1234" with class_name="QDockWidget" (a docked widget), or
    object_name="nameEdit". Add `instance` (pid, id or window title of the app) when
    several Qt apps are running.

    Args:
        ref: Widget ref from qt_snapshot/qt_find_widget. If nothing identifies a widget,
            the active window is captured.
        full_window: If True, captures the first visible top-level window.
        max_width: Max width before downscaling (default 1920).
        max_height: Max height before downscaling (default 1080).
        format: Image format - 'png' or 'jpeg' (default 'png').
        quality: JPEG quality 1-100 (default 80, ignored for PNG).
        describe: Free-text description matched against text, titles, tooltip, what's-this,
            status tip, accessible name, objectName, placeholder, QSS ... (fuzzy).
        window_title: Fuzzy match on the top-level window title.
        class_name: Qt class, subclasses match (e.g. 'QDockWidget').
        object_name: objectName, supports * and ? wildcards.
        search_fields: Limit `describe` to specific fields (see qt_find_widget).
        expand: What to capture relative to the match: 'auto' (a matched label captures the
            widget holding it; anything else captures itself), 'self', 'parent', 'buddy'
            (the label's field) or 'window'.
        index: Which ranked match to use (0 = best).
        min_score: Minimum semantic match score 0-1.

    Returns an image plus a note saying what was captured.
    """
    client = await _ensure_connected(instance)
    notes: list[str] = []
    if not ref and (describe or window_title or class_name or object_name):
        sel = _selector_params(
            describe, window_title, class_name, object_name, search_fields, min_score
        )
        found = await client.call("find_target", {**sel, "expand": expand, "index": index})
        ref = found["ref"]
        t = found["target"]
        label = t.get("objectName") or t.get("text") or ""
        notes.append(f"Captured {t['class']}{f' {label!r}' if label else ''} [ref={ref}].")
        if found.get("note"):
            notes.append(found["note"])
        alts = found.get("alternatives") or []
        if alts:
            notes.append(
                "Other matches: "
                + "; ".join(f"{a['class']} [ref={a['ref']}] score {a['score']}" for a in alts)
            )
    params: dict[str, Any] = {
        "max_width": max_width,
        "max_height": max_height,
        "format": format,
        "quality": quality,
    }
    if ref:
        params["ref"] = ref
    if full_window:
        params["full_window"] = full_window
    result = await client.call("screenshot", params)
    fmt = result.get("format", "png")
    mime = "image/jpeg" if fmt == "jpeg" else "image/png"
    notes.insert(0, f"Size: {result['width']}x{result['height']}")
    return [
        ImageContent(type="image", data=result["image"], mime_type=mime),
        TextContent(type="text", text="\n".join(notes)),
    ]


# --- GIF recording ---


def _gif_result(rec: dict[str, Any], note: str = "") -> list[Any]:
    frames = [(base64.b64decode(f["png"]), float(f["t"])) for f in rec["frames"]]
    if not frames:
        raise ToolError("The recording captured no frames.")
    fps = float(rec.get("fps") or 10.0)
    encoded = encode_gif(frames, last_frame_ms=int(1000 / fps), max_width=GIF_MAX_WIDTH)
    notes = [
        f"GIF: {encoded.width}x{encoded.height}, {len(frames)} frame(s), {encoded.duration_s:.1f}s"
    ]
    if note:
        notes.append(note)
    if rec.get("truncated"):
        notes.append("Stopped early: frame size cap reached (lower max_width or fps).")
    if rec.get("error"):
        notes.append(str(rec["error"]))
    return [
        ImageContent(
            type="image", data=base64.b64encode(encoded.data).decode(), mime_type="image/gif"
        ),
        TextContent(type="text", text="\n".join(notes)),
    ]


async def _start_recording(
    instance: str | int | None,
    ref: str | None,
    fps: float,
    max_seconds: float,
    max_width: int,
    sel: dict[str, Any],
    expand: str,
    index: int,
) -> tuple[str, ProbeClient, dict[str, Any], str]:
    instance_id, client = await _target(instance)
    note = ""
    if not ref and (
        sel["describe"] or sel["window_title"] or sel["class_name"] or sel["object_name"]
    ):
        found = await client.call("find_target", {**sel, "expand": expand, "index": index})
        ref = found["ref"]
        note = found.get("note", "")
    started = await client.call(
        "record_start",
        {"ref": ref, "fps": fps, "max_seconds": max_seconds, "max_width": max_width}
        if ref
        else {"fps": fps, "max_seconds": max_seconds, "max_width": max_width},
    )
    return instance_id, client, started, note


@mcp.tool()
async def qt_record_gif(
    duration: float = 3.0,
    fps: float = 10.0,
    ref: str | None = None,
    describe: str = "",
    window_title: str = "",
    class_name: str = "",
    object_name: str = "",
    search_fields: list[str] | None = None,
    expand: str = "auto",
    index: int = 0,
    min_score: float = 0.5,
    max_width: int = GIF_MAX_WIDTH,
    instance: str | int | None = None,
) -> list[Any]:
    """Record an animated GIF of a window or widget for `duration` seconds.

    Identify the widget exactly like qt_screenshot (ref, or describe/window_title/
    class_name/object_name; the active window if nothing is given). The app keeps running
    normally while recording; interact with it meanwhile via other tools or by hand. Works
    on Windows and Linux (frames come from Qt's own rendering, no screen capture needed).
    For longer or interleaved recordings use qt_record_start / qt_record_stop.
    """
    if not 0 < duration <= 30:
        raise ToolError("duration must be between 0 and 30 seconds (use qt_record_start for more)")
    sel = _selector_params(
        describe, window_title, class_name, object_name, search_fields, min_score
    )
    _, client, started, note = await _start_recording(
        instance, ref, fps, duration + 2.0, max_width, sel, expand, index
    )
    rid = started["recording_id"]
    try:
        await asyncio.sleep(duration)
    finally:
        # Always stop the probe-side timer, even if this call was cancelled.
        rec = await asyncio.shield(client.call("record_stop", {"recording_id": rid}))
    return _gif_result(rec, note)


@mcp.tool()
async def qt_record_start(
    fps: float = 10.0,
    max_seconds: float = 30.0,
    ref: str | None = None,
    describe: str = "",
    window_title: str = "",
    class_name: str = "",
    object_name: str = "",
    search_fields: list[str] | None = None,
    expand: str = "auto",
    index: int = 0,
    min_score: float = 0.5,
    max_width: int = GIF_MAX_WIDTH,
    instance: str | int | None = None,
) -> str:
    """Start a background GIF recording; stop it later with qt_record_stop.

    Returns a recording id that already names the app, so qt_record_stop needs no
    `instance`. Recording stops by itself after `max_seconds`.
    """
    sel = _selector_params(
        describe, window_title, class_name, object_name, search_fields, min_score
    )
    instance_id, _, started, note = await _start_recording(
        instance, ref, fps, max_seconds, max_width, sel, expand, index
    )
    rid = f"{instance_id}:{started['recording_id']}"
    return f"recording_id={rid}" + (f"\n{note}" if note else "")


@mcp.tool()
async def qt_record_stop(recording_id: str) -> list[Any]:
    """Stop a recording started with qt_record_start and return the GIF.

    Args:
        recording_id: The id returned by qt_record_start (includes the app it belongs to).
    """
    instance_id, sep, probe_rid = recording_id.rpartition(":")
    if not sep or not instance_id or not probe_rid:
        raise ToolError(
            "recording_id must look like '<instance>:<id>' as returned by qt_record_start"
        )
    client = await _ensure_connected(instance_id)
    return _gif_result(await client.call("record_stop", {"recording_id": probe_rid}))


# --- Search across every running app ---


@mcp.tool()
async def qt_find_anywhere(
    describe: str = "",
    window_title: str = "",
    class_name: str = "",
    object_name: str = "",
    search_fields: list[str] | None = None,
    min_score: float = 0.5,
    visible_only: bool = True,
    max_results: int = 10,
) -> str:
    """Semantic widget search across ALL running Qt apps at once.

    Use when you do not know which app has the widget. Each hit names its instance; pass
    that id as `instance` to qt_screenshot, qt_click, ... together with the ref.
    """
    live = await _instances.live()
    if not live:
        raise ToolError("No running Qt app with the qtpy-mcp probe was found.")
    sel = _selector_params(
        describe, window_title, class_name, object_name, search_fields, min_score, visible_only
    )
    sel["max_results"] = max_results

    async def one(info_id: str) -> tuple[str, Any]:
        try:
            client = await _instances.client(info_id)
            return info_id, await asyncio.wait_for(client.call("find_semantic", sel), timeout=15)
        except (ToolError, TimeoutError) as exc:
            return info_id, str(exc)

    results = await asyncio.gather(*(one(i.id) for i in live))
    blocks = []
    total = 0
    for info_id, res in results:
        if isinstance(res, str):
            blocks.append(f"{info_id}: unreachable ({res})")
            continue
        total += int(res.get("count", 0))
        if res.get("widgets"):
            blocks.append(_format_widgets(res, header=f"{info_id}: {res['count']} match(es)"))
    if not blocks or total == 0:
        return f"No widgets matched in any of {len(live)} running app(s)."
    return "\n".join(blocks)


@mcp.tool()
async def qt_widget_details(ref: str, instance: str | int | None = None) -> str:
    """Get detailed properties of a specific widget.

    Args:
        ref: Widget ref from qt_snapshot (e.g., 'w5').

    Returns all Qt properties, geometry, parent chain, and more.
    """
    client = await _ensure_connected(instance)
    result = await client.call("widget_details", {"ref": ref})
    lines = [f"Class: {result.get('class', '?')}"]
    if result.get("objectName"):
        lines.append(f"Name: {result['objectName']}")
    if "geometry" in result:
        g = result["geometry"]
        lines.append(f"Geometry: {g['x']},{g['y']} {g['width']}x{g['height']}")
    lines.append(f"Visible: {result.get('visible', '?')}")
    lines.append(f"Enabled: {result.get('enabled', '?')}")
    if "size_hint" in result:
        lines.append(f"Size hint: {result['size_hint'][0]}x{result['size_hint'][1]}")
    if "min_size_hint" in result:
        lines.append(f"Min size hint: {result['min_size_hint'][0]}x{result['min_size_hint'][1]}")
    if "size_policy" in result:
        lines.append(f"Size policy: {result['size_policy']}")
    if "layout" in result:
        lines.append(f"Layout: {result['layout']}")
    if "margins" in result:
        m = result["margins"]
        lines.append(f"Margins: left={m[0]} top={m[1]} right={m[2]} bottom={m[3]}")
    if result.get("parent_chain"):
        lines.append(f"Parent chain: {' > '.join(result['parent_chain'])}")
    if result.get("properties"):
        lines.append("\nProperties:")
        for k, v in result["properties"].items():
            lines.append(f"  {k}: {v}")
    if result.get("items"):
        lines.append(f"\nTree items ({len(result['items'])}):")
        for item in result["items"]:
            indent = "  " * (item.get("depth", 0) + 1)
            lines.append(f"{indent}{item['text']} [ref={item['ref']}]")
    return "\n".join(lines)


# --- Interaction ---


@mcp.tool()
async def qt_click(
    ref: str,
    button: str = "left",
    modifiers: list[str] | None = None,
    position: list[int] | None = None,
    instance: str | int | None = None,
) -> str:
    """Click a widget.

    Args:
        ref: Widget ref from qt_snapshot.
        button: Mouse button - 'left', 'right', or 'middle'.
        modifiers: Keyboard modifiers - 'shift', 'ctrl', 'alt', 'meta'.
        position: [x, y] relative to widget top-left. Default: center.
    """
    client = await _ensure_connected(instance)
    params: dict[str, Any] = {"ref": ref, "button": button}
    if modifiers:
        params["modifiers"] = modifiers
    if position:
        params["position"] = position
    await client.call("click", params)
    return "Clicked."


@mcp.tool()
async def qt_type(
    ref: str,
    text: str,
    clear_first: bool = False,
    use_clipboard: bool = False,
    instance: str | int | None = None,
) -> str:
    """Type text into a widget.

    Args:
        ref: Widget ref from qt_snapshot.
        text: Text to type.
        clear_first: If True, select all and delete before typing.
        use_clipboard: If True, insert via Ctrl+V paste instead of key-by-key events.
            Use this for multi-line text in console/terminal widgets — character-by-character
            typing treats each newline as Enter (submit), corrupting multi-line input.
    """
    client = await _ensure_connected(instance)
    await client.call(
        "type_text",
        {"ref": ref, "text": text, "clear_first": clear_first, "use_clipboard": use_clipboard},
    )
    return "Typed."


@mcp.tool()
async def qt_key_press(key: str, ref: str | None = None, instance: str | int | None = None) -> str:
    """Send a key event to a widget or the focused widget.

    Args:
        key: Key name (e.g., 'Return', 'Escape', 'Ctrl+S', 'a').
        ref: Widget ref. If omitted, sends to the currently focused widget.
    """
    client = await _ensure_connected(instance)
    params: dict[str, Any] = {"key": key}
    if ref:
        params["ref"] = ref
    await client.call("key_press", params)
    return "Key pressed."


@mcp.tool()
async def qt_set_property(
    ref: str, property_name: str, value: str | int | float | bool, instance: str | int | None = None
) -> str:
    """Set a Qt property on a widget.

    Args:
        ref: Widget ref from qt_snapshot.
        property_name: Qt property name.
        value: New value for the property.
    """
    client = await _ensure_connected(instance)
    result = await client.call(
        "set_property", {"ref": ref, "property_name": property_name, "value": value}
    )
    return f"Set {property_name}. Old value: {result.get('old_value', '?')}"


@mcp.tool()
async def qt_invoke_slot(ref: str, method_name: str, instance: str | int | None = None) -> str:
    """Invoke a slot or method on a QObject.

    Args:
        ref: Widget ref from qt_snapshot.
        method_name: Slot/method name to invoke.
    """
    client = await _ensure_connected(instance)
    result = await client.call("invoke_slot", {"ref": ref, "method_name": method_name})
    ok = result.get("ok", False)
    return f"Invoked {method_name}: {'success' if ok else 'failed'}"


@mcp.tool()
async def qt_get_text(ref: str, instance: str | int | None = None) -> str:
    """Extract text content from a text editor or input widget.

    Args:
        ref: Widget ref from qt_snapshot (e.g., 'w5').

    Works with QPlainTextEdit, QTextEdit, QLineEdit, QLabel, QComboBox,
    and any widget with a text() or toPlainText() method.
    """
    client = await _ensure_connected(instance)
    result = await client.call("get_text", {"ref": ref})
    text = result["text"]
    length = result["length"]
    line_count = result.get("line_count", "?")
    read_only = result.get("read_only")

    header = f"Text ({length} chars, {line_count} lines)"
    if read_only:
        header += " [read-only]"

    if length > MAX_TEXT_PREVIEW_CHARS:
        display_text = (
            text[:MAX_TEXT_PREVIEW_CHARS]
            + f"\n\n... ({length - MAX_TEXT_PREVIEW_CHARS} more chars)"
        )
    else:
        display_text = text

    return f"{header}\n\n{display_text}"


@mcp.tool()
async def qt_trigger_action(
    ref: str,
    action_text: str | None = None,
    action_index: int | None = None,
    instance: str | int | None = None,
) -> str:
    """Trigger a menu action or toolbar action by text or index.

    Args:
        ref: Ref to a QMenu, QMenuBar, QToolBar, or any widget with actions.
        action_text: The action's display text (e.g., 'Save', '&Open').
            Ampersands are stripped for matching.
        action_index: The 0-based index of the action
            (matching qt_menu_items order).

    Provide either action_text or action_index (not both).
    Use qt_menu_items first to see available actions.
    """
    if (action_text is None) == (action_index is None):
        raise ToolError("Provide exactly one of action_text or action_index")

    client = await _ensure_connected(instance)
    params: dict[str, Any] = {"ref": ref}
    if action_text is not None:
        params["action_text"] = action_text
    if action_index is not None:
        params["action_index"] = action_index
    result = await client.call("trigger_action", params)
    return f"Triggered: {result.get('action_text', '?')}"


@mcp.tool()
async def qt_wait_for(
    condition: str,
    timeout_ms: int = DEFAULT_WAIT_TIMEOUT_MS,
    object_name: str | None = None,
    ref: str | None = None,
    property_name: str | None = None,
    value: str | int | float | bool | None = None,
    instance: str | int | None = None,
) -> str:
    """Wait for a UI state change.

    Args:
        condition: One of 'widget_visible', 'window_count_changed', 'property_equals'.
        timeout_ms: Max time to wait in milliseconds (default 5000).
        object_name: Widget objectName (for widget_visible).
        ref: Widget ref (for property_equals).
        property_name: Property name (for property_equals).
        value: Expected value (for property_equals).
    """
    client = await _ensure_connected(instance)
    params: dict[str, Any] = {"condition": condition, "timeout_ms": timeout_ms}
    if object_name:
        params["object_name"] = object_name
    if ref:
        params["ref"] = ref
    if property_name:
        params["property_name"] = property_name
    if value is not None:
        params["value"] = value
    result = await client.call("wait_for", params)
    elapsed = result.get("elapsed_ms", 0)
    return f"Condition met after {elapsed}ms."


@mcp.tool()
async def qt_batch(steps: list[dict[str, Any]], instance: str | int | None = None) -> str:
    """Execute multiple Qt operations in a single round trip.

    Chains clicks, types, key presses, waits, snapshots, and reads into one
    server call — dramatically reduces latency compared to sequential tool calls.

    Each step dict:
        method  (str, required): RPC method — same names as probe methods:
            click, type_text, key_press, set_property, invoke_slot, get_text,
            trigger_action, wait_for, snapshot, find_widget, screenshot.
            Special: "wait" with params {"ms": N} sleeps N ms processing events.
        params  (dict, optional): Parameters forwarded to the method.
        wait_ms (int, optional): Extra pause after the step (default 0).

    Execution stops on the first error; partial results are returned.
    Screenshot steps return size only (not base64 data) — call qt_screenshot
    separately when you actually need the image.

    Note: refs must be known before calling qt_batch — steps cannot reference
    results from earlier steps within the same batch. Use qt_find_widget first
    to obtain refs, then pass them into the batch call.

    Example — click a button twice then read a label in one round trip:
        qt_batch([
            {"method": "click",    "params": {"ref": "w5"}},
            {"method": "click",    "params": {"ref": "w5"}},
            {"method": "get_text", "params": {"ref": "w12"}},
        ])

    Example — type multi-line code into IPython console and wait for output:
        qt_batch([
            {"method": "type_text", "params": {"ref": "w42", "text": "%run /tmp/x.py",
                                               "use_clipboard": true}},
            {"method": "key_press", "params": {"key": "Return"}},
            {"method": "wait",      "params": {"ms": 3000}},
            {"method": "snapshot",  "params": {"max_depth": 4}},
        ])
    """
    client = await _ensure_connected(instance)
    result = await client.call("batch", {"steps": steps})

    results = result.get("results", [])
    completed = result.get("completed", 0)
    failed_at = result.get("failed_at")
    total = len(steps)

    header = f"Batch: {completed}/{total} steps completed"
    if failed_at is not None:
        header += f", failed at step {failed_at}"
    lines = [header]

    for i, r in enumerate(results):
        method = steps[i].get("method", "?") if i < len(steps) else "?"
        if not r.get("ok"):
            lines.append(f"  [{i}] {method}: ERROR — {r.get('error', '?')}")
            continue

        res = r.get("result", {})
        if not isinstance(res, dict):
            lines.append(f"  [{i}] {method}: OK")
            continue

        # Format result compactly, inlining useful content
        if "tree" in res:
            tree_lines = res["tree"].splitlines()
            lines.append(
                f"  [{i}] {method}: {res.get('widget_count', '?')} widgets "
                f"(gen {res.get('generation', '?')})"
            )
            for tl in tree_lines[:40]:  # cap inline tree at 40 lines
                lines.append(f"    {tl}")
            if len(tree_lines) > 40:
                lines.append(f"    ... ({len(tree_lines) - 40} more lines)")
        elif "widgets" in res:
            lines.append(f"  [{i}] {method}: {res.get('count', 0)} widget(s) found")
            for w in res.get("widgets", []):
                name = f' "{w["objectName"]}"' if w.get("objectName") else ""
                lines.append(f"    - {w['class']}{name} [ref={w['ref']}]")
        elif "text" in res:
            preview = res["text"][:200].replace("\n", "\\n")
            lines.append(
                f"  [{i}] {method}: {res.get('length', '?')} chars — {preview}"
                + ("..." if res.get("length", 0) > 200 else "")
            )
        elif "width" in res and "height" in res and "image" in res:
            # Screenshot: return size only, not the base64 blob
            lines.append(f"  [{i}] {method}: screenshot {res['width']}x{res['height']}")
        elif "waited_ms" in res:
            lines.append(f"  [{i}] {method}: waited {res['waited_ms']}ms")
        elif "ok" in res:
            lines.append(f"  [{i}] {method}: {'OK' if res['ok'] else 'FAIL'}")
        else:
            summary = ", ".join(f"{k}={v!r}" for k, v in list(res.items())[:4])
            lines.append(f"  [{i}] {method}: {summary}")

    return "\n".join(lines)


# --- Application State ---


@mcp.tool()
async def qt_list_windows(skip_hidden: bool = True, instance: str | int | None = None) -> str:
    """List all top-level windows and their types."""
    client = await _ensure_connected(instance)
    params: dict[str, Any] = {}
    if skip_hidden:
        params["skip_hidden"] = skip_hidden
    result = await client.call("list_windows", params)
    lines = []
    for w in result.get("windows", []):
        vis = "" if w.get("visible") else " [hidden]"
        lines.append(f'- {w["class"]} "{w.get("objectName", "")}" {w["size"]}{vis}')
    return "\n".join(lines) or "(no windows)"


@mcp.tool()
async def qt_object_tree(
    root_ref: str | None = None,
    max_depth: int = DEFAULT_SNAPSHOT_DEPTH,
    instance: str | int | None = None,
) -> str:
    """Get the full QObject parent-child tree (not just visible widgets).

    Args:
        root_ref: Starting ref. Defaults to QApplication root.
        max_depth: Maximum traversal depth.
    """
    client = await _ensure_connected(instance)
    params: dict[str, Any] = {"max_depth": max_depth}
    if root_ref:
        params["root_ref"] = root_ref
    result = await client.call("object_tree", params)
    count = result.get("count", 0)
    tree = result.get("tree", "")
    return f"Objects: {count}\n\n{tree}"


@mcp.tool()
async def qt_active_popup(instance: str | int | None = None) -> str:
    """Check for active popup or modal dialog widgets."""
    client = await _ensure_connected(instance)
    result = await client.call("active_popup")
    parts = []
    if result.get("popup"):
        p = result["popup"]
        parts.append(f'Popup: {p["class"]} "{p.get("objectName", "")}" {p["size"]}')
    if result.get("modal"):
        m = result["modal"]
        parts.append(f'Modal: {m["class"]} "{m.get("objectName", "")}" {m["size"]}')
    return "\n".join(parts) or "No active popup or modal."


@mcp.tool()
async def qt_menu_items(ref: str, instance: str | int | None = None) -> str:
    """Get actions from a QMenu widget.

    Args:
        ref: Ref to a QMenu widget.
    """
    client = await _ensure_connected(instance)
    result = await client.call("menu_items", {"ref": ref})
    actions = result.get("actions", [])
    if not actions:
        return "(empty menu)"
    lines = [f"Menu actions ({len(actions)}):"]
    for a in actions:
        if a.get("separator"):
            lines.append("  ---")
            continue
        flags = []
        if not a.get("enabled", True):
            flags.append("disabled")
        if a.get("checked"):
            flags.append("checked")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"  - {a['text']}{suffix}")
    return "\n".join(lines)


# --- Diagnostics ---


@mcp.tool()
async def qt_messages(
    level: str = "info",
    instance: str | int | None = None,
) -> str:
    """Returns all console messages

    Args:
        level: Level of the console messages to return. Each level includes the messages
            of more severe levels. Defaults to "info".
    """
    client = await _ensure_connected(instance)
    result = await client.call("qt_messages", {"level": level})
    messages = result.get("messages", [])
    if not messages:
        return "(no Qt messages captured)"
    lines = [f"Qt messages ({len(messages)}):"]
    for m in messages:
        lines.append(f"  [{m['level'].upper()}] {m['message']}")
    return "\n".join(lines)


@mcp.tool()
async def qt_thread_check(instance: str | int | None = None) -> str:
    """Check thread affinity of all QObjects to detect threading issues.

    Reports which threads own QObjects, flags QWidget subclasses on non-GUI
    threads (the #1 cause of GUI freezes), and lists QThread instances.
    """
    client = await _ensure_connected(instance)
    result = await client.call("thread_check", {})
    lines = [f"GUI thread: {result['gui_thread']}"]
    lines.append(f"\nThreads ({len(result['threads'])}):")
    for t in result["threads"]:
        lines.append(
            f"  - {t['name']} (id={t['id']}): "
            f"{t['object_count']} objects, {t['widget_count']} widgets"
        )
    if result.get("warnings"):
        lines.append(f"\nWARNINGS ({len(result['warnings'])}):")
        for w in result["warnings"]:
            lines.append(f'  - {w["class"]} "{w["object_name"]}" on thread {w["thread_name"]}')
    else:
        lines.append("\nNo threading issues detected.")
    if result.get("qthreads"):
        lines.append(f"\nQThread instances ({len(result['qthreads'])}):")
        for q in result["qthreads"]:
            status = "running" if q["running"] else ("finished" if q["finished"] else "not started")
            lines.append(f"  - {q['object_name'] or '(unnamed)'}: {status}")
    return "\n".join(lines)


@mcp.tool()
async def qt_signals(ref: str, instance: str | int | None = None) -> str:
    """Inspect signal connections on a QObject.

    For a given widget/object, enumerates all signals and reports which are
    connected and how many receivers each has. Useful for debugging "nothing
    happens when I click" problems.

    Args:
        ref: Widget ref from qt_snapshot (e.g., 'w5').
    """
    client = await _ensure_connected(instance)
    result = await client.call("signals", {"ref": ref})
    signals = result.get("signals", [])
    if not signals:
        return f"No signals found on {result.get('class', '?')}"
    lines = [f"Signals on {result.get('class', '?')} ({len(signals)}):"]
    for s in signals:
        connected = "connected" if s["connected"] else "not connected"
        lines.append(f"  - {s['signature']}: {connected} ({s['receiver_count']} receivers)")
    return "\n".join(lines)


@mcp.tool()
async def qt_layout_check(instance: str | int | None = None) -> str:
    """Detect layout issues in the visible widget tree.

    Scans all visible widgets and reports problems like:
    - zero_size: Visible widget with 0x0 geometry (collapsed)
    - no_layout: Container with visible children but no layout manager
    - smaller_than_hint: Widget smaller than its sizeHint (content likely clipped)
    - text_truncated: Text content wider than widget width
    - overlapping_siblings: Sibling widgets with intersecting geometries
    """
    client = await _ensure_connected(instance)
    result = await client.call("layout_check", {})
    issues = result.get("issues", [])
    scanned = result.get("scanned", 0)
    if not issues:
        return f"No layout issues detected ({scanned} widgets scanned)."
    lines = [f"Layout issues ({len(issues)}, {scanned} widgets scanned):"]
    for issue in issues:
        severity = issue["severity"].upper()
        lines.append(
            f"  [{severity}] {issue['type']}: {issue['widget_class']} "
            f'"{issue.get("object_name", "")}" [ref={issue["ref"]}] — {issue["detail"]}'
        )
    return "\n".join(lines)


# --- QGraphicsScene ---


@mcp.tool()
async def qt_scene_snapshot(ref: str, instance: str | int | None = None) -> str:
    """Get all items in a QGraphicsScene.

    Args:
        ref: Ref to a QGraphicsView widget.
    """
    client = await _ensure_connected(instance)
    result = await client.call("scene_snapshot", {"ref": ref})
    items = result.get("items", [])
    if not items:
        return "(empty scene)"
    lines = [f"Scene items ({len(items)}):"]
    for i, item in enumerate(items):
        pos = item.get("pos", [0, 0])
        bounds = item.get("bounds", [0, 0])
        vis = "" if item.get("visible", True) else " [hidden]"
        text = f' "{item["text"]}"' if "text" in item else ""
        lines.append(
            f"  [{i}] {item['class']}{text} pos=({pos[0]:.0f},{pos[1]:.0f}) "
            f"size=({bounds[0]:.0f}x{bounds[1]:.0f}) z={item.get('z', 0)}{vis}"
        )
    return "\n".join(lines)


# --- VTK/3D ---


@mcp.tool()
async def qt_vtk_scene_info(ref: str, instance: str | int | None = None) -> str:
    """Get VTK scene state from a PyVista/VTK widget.

    Args:
        ref: Ref to a widget containing a VTK render window.
    """
    client = await _ensure_connected(instance)
    result = await client.call("vtk_scene_info", {"ref": ref})

    lines = []
    if result.get("camera"):
        cam = result["camera"]
        lines.append(f"Camera position: {cam.get('position')}")
        lines.append(f"Camera focal point: {cam.get('focal_point')}")
        lines.append(f"Camera view up: {cam.get('view_up')}")

    lines.append(f"Background: {result.get('background')}")
    lines.append(f"Window size: {result.get('size')}")

    actors = result.get("actors", [])
    lines.append(f"\nActors ({len(actors)}):")
    for a in actors:
        vis = "visible" if a.get("visibility") else "hidden"
        lines.append(f"  - {a.get('class', '?')} [{vis}] bounds={a.get('bounds')}")
        if "points" in a:
            lines.append(f"    data: {a['points']} points, {a['cells']} cells")

    return "\n".join(lines)


@mcp.tool()
async def qt_vtk_screenshot(ref: str, instance: str | int | None = None) -> list[Any]:
    """Capture a VTK render window to an image.

    Args:
        ref: Ref to a widget containing a VTK render window.
    """
    client = await _ensure_connected(instance)
    result = await client.call("vtk_screenshot", {"ref": ref})
    return [
        ImageContent(type="image", data=result["image"], mime_type="image/png"),
        TextContent(type="text", text=f"Size: {result['width']}x{result['height']}"),
    ]
