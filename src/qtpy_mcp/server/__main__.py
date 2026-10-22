"""Entry point: python -m qtpy_mcp.server"""

from qtpy_mcp.server.mcp_server import mcp


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
