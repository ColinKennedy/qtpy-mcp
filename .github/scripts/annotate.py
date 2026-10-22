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
    # Annotations are size-limited (a few KB), so drop the noise and keep the failures.
    interesting = [
        ln for ln in lines if " PASSED" not in ln and " SKIPPED" not in ln and ln.strip()
    ]
    text = "\n".join(interesting)
    if len(text) > 3500:
        text = text[:1200] + "\n...\n" + text[-2300:]
    body = text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error title={title}::{body}")  # noqa: T201


if __name__ == "__main__":
    main()
