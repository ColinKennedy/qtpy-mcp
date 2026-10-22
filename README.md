# qtpy-mcp

MCP server that lets an AI agent **find, screenshot, GIF-record and drive Qt widgets** in
running applications and DCCs (Houdini, Blender, Maya, Nuke, ...), built on
[Qt.py](https://github.com/mottosso/Qt.py) so it works with PySide6, PyQt6, PySide2 and PyQt5.

It is a fork of [0xCarbon/qt-mcp](https://github.com/0xCarbon/qt-mcp) (MIT OR Apache-2.0):
same tool set, plus Qt.py, many-apps-at-once routing, semantic widget search and GIFs.

```
 "screenshot the PIPE-1234 window's docked Render Queue"      ┌────────────────────┐
 "now the widget labelled 'Name goes here:' in DEV-1515"  ──► │  Houdini  pid 4121 │ probe
 "record a GIF of Houdini process 1252151's 'Foo bar'"        ├────────────────────┤
        │                                                      │  Houdini  pid 1252 │ probe
   MCP client ──stdio──► qtpy-mcp ── registry + JSON-RPC ───► ├────────────────────┤
                         (routes)    (127.0.0.1, token)        │  Blender  pid 7770 │ probe
                                                               └────────────────────┘
```

## What you can ask for

- **Find by meaning**, not by ref: text, window title, class, `objectName`, placeholder,
  **tooltip, whatsThis, statusTip**, accessible name, tab text, dynamic properties, even
  **style sheet**. Fuzzy-ranked; each hit says which field matched.
- **Screenshot** a whole window, a dialog, or any sub-widget such as a docked panel. A match on a
  label like `Name goes here:` captures the form around it (`expand="auto"`; also `self`,
  `parent`, `buddy`, `window`).
- **Record a GIF** (`qt_record_gif`, or `qt_record_start` / `qt_record_stop` while you interact).
  The probe grabs frames with `QWidget.grab()` and Pillow encodes them, so it behaves the same
  on Windows, Linux and macOS: no ffmpeg or screen-capture APIs.
- **Many applications at once.** Several Houdinis, Blender and Maya can be open together; one
  agent connection addresses any of them (see below).
- Everything upstream provides: widget tree snapshots, properties, click / type / keys,
  menus, signals, layout checks, QGraphicsScene and VTK inspection.

## Install

Two pieces: the **probe** lives inside the Qt application; the **server** runs next to your
MCP client.

```bash
# MCP client side (any Python 3.10+):
uvx qtpy-mcp                      # or: pip install qtpy-mcp

# Inside a DCC, whose Python already has a Qt binding: install without dependencies
hython -m pip install --no-deps qtpy-mcp Qt.py          # Houdini
<blender-python> -m pip install --no-deps qtpy-mcp Qt.py PySide6-Essentials   # Blender
```

Standalone apps can use an extra: `pip install "qtpy-mcp[pyside6]"` (or `pyqt6`, `pyqt5`, `pyside2`).
Choose a binding with `QT_PREFERRED_BINDING=PyQt6` (Qt.py's own variable).

MCP client config:

```json
{ "mcpServers": { "qtpy": { "command": "uvx", "args": ["qtpy-mcp"] } } }
```

## Put the probe in your app

```python
from qtpy_mcp.probe import install

install(name="houdini")  # after the QApplication exists
```

or set `QTPY_MCP_PROBE=1` in the environment before launching (no code change).
Working examples: [`examples/houdini`](examples/houdini/qt_in_houdini.py),
[`examples/blender`](examples/blender/qt_in_blender.py).

## Talking to several apps

Every tool takes an optional `instance`. The server finds running apps through a per-user
registry, so apps may start and stop at any time. A reference can be any of:

| You say | `instance` |
|---|---|
| a window title or part of one | `"PIPE-1234"`, `"DEV-1515"` |
| a process id | `1252151`, `"1252151"`, `"pid:1252151"`, `"houdini-1252151"` |
| an app name (if unique) | `"blender"` |
| the instance id | `"houdini-1252151"` |

If a reference matches more than one app, the tool errors with the candidates (pid, name,
window titles) rather than guessing. With one app running, `instance` can be omitted.
`qt_list_instances` shows what is running; `qt_find_anywhere` searches every app for a
description and names the owner of each hit; `qt_select_instance` sets a default.

Routing is covered by tests against fake probes, and against real separate Qt processes
(`tests/test_multiprocess.py`).

## Tools

| | |
|---|---|
| Instances | `qt_list_instances`, `qt_select_instance`, `qt_find_anywhere` |
| Finding | `qt_find_widget` (semantic: `describe`, `window_title`, `class_name`, `object_name`, `search_fields`, `min_score`), `qt_snapshot`, `qt_object_tree`, `qt_list_windows` |
| Pictures | `qt_screenshot` (by ref or by description), `qt_record_gif`, `qt_record_start`, `qt_record_stop`, `qt_vtk_screenshot` |
| Driving | `qt_click`, `qt_type`, `qt_key_press`, `qt_trigger_action`, `qt_set_property`, `qt_invoke_slot`, `qt_wait_for`, `qt_batch` |
| Inspecting | `qt_widget_details`, `qt_get_text`, `qt_menu_items`, `qt_active_popup`, `qt_signals`, `qt_layout_check`, `qt_thread_check`, `qt_messages`, `qt_scene_snapshot`, `qt_vtk_scene_info` |

## Configuration

| Variable | Meaning |
|---|---|
| `QTPY_MCP_PROBE=1` | auto-install the probe when a `QApplication` is created |
| `QTPY_MCP_NAME` | label for this instance (default: application name) |
| `QTPY_MCP_PORT` | fixed probe port (default: ephemeral); on the server side, connect to this one port and skip the registry |
| `QTPY_MCP_HOST` | host the server connects to in fixed-port mode |
| `QTPY_MCP_TOKEN` | shared secret instead of a random one |
| `QTPY_MCP_REGISTRY_DIR` | where instances register (default: per-user runtime dir) |
| `QTPY_MCP_SCREENSHOT_MAX_WIDTH` / `_HEIGHT` | cap screenshot size |
| `QT_PREFERRED_BINDING` | Qt.py: `PySide6`, `PyQt6`, `PySide2`, `PyQt5` |

## Security

The probe can click, type and call slots in the host application. It listens on `127.0.0.1`
only, on an ephemeral port, and every request needs a per-process token kept in a registry
file readable only by your user. See [SECURITY.md](SECURITY.md).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). Quality gates in CI: `ruff`, `mypy --strict`, tests on
PySide6 / PyQt6 / PyQt5 (Linux, Windows, macOS), a Blender smoke test, build checks and
`pip-audit`. Pushing a `vX.Y.Z` tag publishes to PyPI.

## License

MIT OR Apache-2.0, at your option ([LICENSE-MIT](LICENSE-MIT), [LICENSE-APACHE](LICENSE-APACHE)).
Portions derive from `qt-mcp`; see [NOTICE](NOTICE).
