"""Entry point: ``qtpy-mcp`` / ``python -m qtpy_mcp.server`` (MCP over stdio)."""

from __future__ import annotations

import sys

from qtpy_mcp import __version__


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if "--version" in args:
        print(f"qtpy-mcp {__version__}")  # noqa: T201
        return
    if "-h" in args or "--help" in args:
        print(  # noqa: T201
            f"qtpy-mcp {__version__}\n\n"
            "MCP server (stdio) for Qt apps and DCCs. Start it from your MCP client; it finds\n"
            "every running app that has the probe installed (see README) and routes tool\n"
            "calls by window title, process id, name or description.\n\n"
            "  --version   print the version and exit\n"
        )
        return
    from qtpy_mcp.server.mcp_server import mcp

    mcp.run()


if __name__ == "__main__":
    main()
