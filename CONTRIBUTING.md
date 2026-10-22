# Contributing

Thanks for contributing to `qtpy-mcp`.

## Development setup

```bash
git clone <this repository>
cd qtpy-mcp
uv sync --group dev --extra pyside6      # or pyqt6 / pyqt5 / pyside2
```

## Local checks

Run these before opening a pull request (CI runs the same):

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                       # strict
uv run pytest -v                  # needs a Qt binding; set QT_PREFERRED_BINDING to choose
```

Tests that do not need Qt (routing, registry, GIF encoding) run without any binding:

```bash
uv run pytest tests/test_registry.py tests/test_instances.py tests/test_semantic_tools.py
```

`tests/test_multiprocess.py` starts several real offscreen Qt processes and checks that
requests are routed to the right one by window title, process id, name and description.
If you change routing, extend it.

Qt.py ships no type information, so `stubs/Qt/` holds permissive stubs for mypy. If you
use a new Qt class in `src/`, add it there.

## Pull requests

- Keep PRs focused. Update `README.md` for user-facing changes and `CHANGELOG.md` for notable ones.
- Make sure CI passes.

## Releasing (maintainers)

A release is a tag push; CI does the rest (tests, build, PyPI via trusted publishing, GitHub release).

1. Set `__version__` in `src/qtpy_mcp/__init__.py` and add a `## [X.Y.Z] - date` section to `CHANGELOG.md`.
2. Commit, then `git tag vX.Y.Z && git push origin main vX.Y.Z`.

The release workflow refuses to run if the tag and `__version__` differ or the changelog
section is missing, and runs the full CI matrix before anything is uploaded.

### One-time PyPI setup

No API token is stored in GitHub. Instead, on <https://pypi.org/manage/account/publishing/>
add a *pending trusted publisher*:

| Field | Value |
|---|---|
| PyPI project name | `qtpy-mcp` |
| Owner / repository | this GitHub repository |
| Workflow name | `pypi.yml` |
| Environment name | `pypi` |

Then create a GitHub environment named `pypi` (Settings → Environments); adding required
reviewers there gives you a manual approval before each upload if you want one.

## Licensing

By submitting a contribution, you agree that your contribution is licensed under
`MIT OR Apache-2.0`.
