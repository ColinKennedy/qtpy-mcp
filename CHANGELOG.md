# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-22

First release of `qtpy-mcp`, a fork of [0xCarbon/qt-mcp](https://github.com/0xCarbon/qt-mcp)
(MIT OR Apache-2.0) rebuilt for Qt.py and for live DCC sessions.

### Added

- Qt.py everywhere: PySide6, PyQt6, PySide2 and PyQt5 all work; inside a DCC the host's
  binding is used. Select one with `QT_PREFERRED_BINDING`.
- Many applications at once: each probe registers itself (pid, name, port, token) in a
  per-user registry; one MCP connection addresses any of them with the `instance` argument,
  matched by window title, process id (`1252151`, `pid:1252151`, `houdini-1252151`), name or
  id. Ambiguous references return an error listing the candidates. New tools
  `qt_list_instances`, `qt_select_instance`, `qt_find_anywhere`.
- Semantic widget search by text, window title, class, objectName, placeholder, tooltip,
  whatsThis, statusTip, accessible name/description, tab text, dynamic properties and
  style sheet (`qt_find_widget`, `qt_screenshot` with `describe=`), with fuzzy ranking.
- GIF recording that works on Windows, Linux and macOS: `qt_record_gif`,
  `qt_record_start`, `qt_record_stop`.
- Probe authentication with a per-process token; ephemeral ports.
- Examples for Blender and Houdini; `mypy --strict`, ruff and a Qt-binding test matrix in CI;
  tag-triggered PyPI releases via trusted publishing.

### Changed

- Package and module renamed to `qtpy_mcp`; Python 3.10+ (upstream required 3.12).
- Errors raised by tools are now visible to the model (`ToolError`), on `mcp` 2.x.

### Credits

- Tool set, probe and test suite originate from `qt-mcp` by the 0xCarbon contributors.
