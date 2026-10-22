"""SemanticFinder against real widgets (needs a Qt binding; offscreen)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.qt


@pytest.fixture
def ui(qapp: Any) -> Iterator[dict[str, Any]]:
    from qtpy_mcp.probe._qt import QtCore, QtWidgets, scoped

    win = QtWidgets.QMainWindow()
    win.setWindowTitle("PIPE-1234 - Asset Publisher")
    body = QtWidgets.QWidget()
    form = QtWidgets.QFormLayout(body)

    name_label = QtWidgets.QLabel("Name goes here:")
    name_edit = QtWidgets.QLineEdit()
    name_edit.setObjectName("assetNameField")
    name_edit.setPlaceholderText("e.g. hero_rig")
    name_label.setBuddy(name_edit)
    form.addRow(name_label, name_edit)

    publish = QtWidgets.QPushButton("Go")
    publish.setObjectName("publishButton")
    publish.setToolTip("Publish the selected asset to the shared library")
    publish.setWhatsThis("Uploads the current asset and notifies the pipeline team")
    publish.setStatusTip("Push to production")
    publish.setStyleSheet("background-color: #ff6600;")
    form.addRow(publish)

    win.setCentralWidget(body)
    dock = QtWidgets.QDockWidget("Render Queue", win)
    dock.setObjectName("renderQueueDock")
    dock.setWidget(QtWidgets.QListWidget())
    win.addDockWidget(scoped(QtCore.Qt).DockWidgetArea.RightDockWidgetArea, dock)
    win.show()
    qapp.processEvents()
    yield {"win": win, "label": name_label, "edit": name_edit, "button": publish, "dock": dock}
    win.close()
    qapp.processEvents()


def _finder() -> Any:
    from qtpy_mcp.probe.finder import SemanticFinder
    from qtpy_mcp.probe.ref_registry import RefRegistry

    reg = RefRegistry()
    return SemanticFinder(reg), reg


def test_find_by_label_text(ui: dict[str, Any]) -> None:
    finder, reg = _finder()
    hits = finder.find(describe="Name goes here:")["widgets"]
    assert hits
    assert reg.resolve_or_raise(hits[0]["ref"]) is ui["label"]


def test_label_expands_to_its_parent_container_by_default(ui: dict[str, Any]) -> None:
    finder, reg = _finder()
    info = finder.find_target(describe="Name goes here:")
    target = reg.resolve_or_raise(info["ref"])
    assert target is ui["label"].parentWidget()
    assert "label" in info["note"].lower()


@pytest.mark.parametrize(
    ("expand", "expected"),
    [("self", "label"), ("buddy", "edit"), ("window", "win")],
)
def test_expand_modes(ui: dict[str, Any], expand: str, expected: str) -> None:
    finder, reg = _finder()
    info = finder.find_target(describe="Name goes here:", expand=expand)
    assert reg.resolve_or_raise(info["ref"]) is ui[expected]


@pytest.mark.parametrize(
    "description",
    [
        "Publish the selected asset",  # tooltip
        "notifies the pipeline team",  # whatsThis
        "Push to production",  # statusTip
        "publishButton",  # objectName
    ],
)
def test_find_by_tooltip_whatsthis_statustip_and_id(ui: dict[str, Any], description: str) -> None:
    finder, reg = _finder()
    hits = finder.find(describe=description)["widgets"]
    assert hits, description
    assert reg.resolve_or_raise(hits[0]["ref"]) is ui["button"]


def test_find_reports_which_field_matched(ui: dict[str, Any]) -> None:
    finder, _ = _finder()
    top = finder.find(describe="Push to production")["widgets"][0]
    assert top["matched_field"] == "status_tip"
    assert top["score"] > 0.5


def test_find_by_placeholder_and_object_name(ui: dict[str, Any]) -> None:
    finder, reg = _finder()
    by_placeholder = finder.find(describe="hero_rig")["widgets"][0]
    assert reg.resolve_or_raise(by_placeholder["ref"]) is ui["edit"]
    by_id = finder.find(object_name="assetNameField")["widgets"][0]
    assert reg.resolve_or_raise(by_id["ref"]) is ui["edit"]


def test_find_by_stylesheet(ui: dict[str, Any]) -> None:
    finder, reg = _finder()
    hits = finder.find(describe="#ff6600", search_fields=["style_sheet"], min_score=0.2)["widgets"]
    assert hits
    assert reg.resolve_or_raise(hits[0]["ref"]) is ui["button"]


def test_find_by_class_name(ui: dict[str, Any]) -> None:
    finder, reg = _finder()
    hits = finder.find(class_name="QDockWidget")["widgets"]
    assert [reg.resolve_or_raise(h["ref"]) for h in hits] == [ui["dock"]]


def test_dock_is_found_by_its_title_and_window_title_scopes_the_search(
    ui: dict[str, Any],
) -> None:
    finder, reg = _finder()
    info = finder.find_target(window_title="PIPE-1234", class_name="QDockWidget")
    assert reg.resolve_or_raise(info["ref"]) is ui["dock"]
    by_title = finder.find_target(describe="Render Queue")
    assert reg.resolve_or_raise(by_title["ref"]) is ui["dock"]
    with pytest.raises(ValueError, match="No widget matched"):
        finder.find_target(window_title="DEV-1515", class_name="QDockWidget")


def test_no_match_gives_actionable_error(ui: dict[str, Any]) -> None:
    finder, _ = _finder()
    with pytest.raises(ValueError, match="No widget matched"):
        finder.find_target(describe="zzzzzz nothing like this qqqq")


def test_index_selects_a_lower_ranked_match(ui: dict[str, Any]) -> None:
    finder, reg = _finder()
    first = finder.find_target(class_name="QPushButton", index=0)
    assert reg.resolve_or_raise(first["ref"]) is ui["button"]
    with pytest.raises(ValueError, match="out of range"):
        finder.find_target(class_name="QPushButton", index=5)
