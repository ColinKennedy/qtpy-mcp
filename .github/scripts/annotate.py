"""Re-emit the tail of a command's output as a GitHub error annotation.

Job logs are not always reachable (for example from sandboxed tooling), annotations are:
they are served by the check-runs API. Usage: annotate.py TITLE FILE
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    title, path = sys.argv[1], Path(sys.argv[2])
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    keep = lines if len(lines) <= 160 else [*lines[:50], "...", *lines[-100:]]
    body = "\n".join(keep).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error title={title}::{body[:60000]}")  # noqa: T201


if __name__ == "__main__":
    main()
