"""Semantic widget search: find a widget from a description instead of a ref.

Matches a free-text description fuzzily against label/button text, group/dock titles,
tooltip, what's-this, status tip, accessible name/description, objectName (the "ID"),
placeholder, tab titles, dynamic properties, class name and style sheet. Structured
filters (window title, class, objectName) are ANDed with it.

Runs inside the target app: standard library + Qt.py only.
"""

from __future__ import annotations

import fnmatch
import html
import re
from typing import Any

from qtpy_mcp.probe._qt import QtWidgets
from qtpy_mcp.probe.fuzzy import FIELD_WEIGHTS, best_match, score
from qtpy_mcp.probe.ref_registry import RefRegistry

QApplication = QtWidgets.QApplication
QWidget = QtWidgets.QWidget
QLabel = QtWidgets.QLabel

EXPAND_MODES = ("auto", "self", "parent", "buddy", "window")
_TAGS = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    if "<" in text:
        text = html.unescape(_TAGS.sub(" ", text))
    return text.strip()


def _call_str(widget: Any, method: str) -> str:
    fn = getattr(widget, method, None)
    if not callable(fn):
        return ""
    try:
        value = fn()
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _dynamic_properties(widget: Any) -> str:
    parts: list[str] = []
    try:
        names = widget.dynamicPropertyNames()
    except Exception:
        return ""
    for raw in names:
        try:
            key = bytes(raw).decode("utf-8", "ignore")
            value = widget.property(key)
        except Exception:
            continue
        if isinstance(value, str | int | float | bool):
            parts.append(f"{key} {value}")
    return " | ".join(parts)


def widget_fields(widget: Any) -> dict[str, str]:
    """Every searchable description of ``widget`` (keys match ``fuzzy.FIELD_WEIGHTS``)."""
    text = ""
    for method in ("text", "currentText", "toPlainText"):
        text = _plain(_call_str(widget, method))
        if text:
            break
    tabs = ""
    if hasattr(widget, "tabText") and hasattr(widget, "count"):
        try:
            tabs = " | ".join(str(widget.tabText(i)) for i in range(int(widget.count())))
        except Exception:
            tabs = ""
    is_window = bool(widget.isWindow())
    own_title = _call_str(widget, "windowTitle")
    return {
        "text": text[:500],
        # QGroupBox.title(), or a QDockWidget's/QMdiSubWindow's window title
        "title": _call_str(widget, "title") or ("" if is_window else own_title),
        "window_title": own_title if is_window else "",
        "placeholder": _call_str(widget, "placeholderText"),
        "tooltip": _plain(_call_str(widget, "toolTip")),
        "whats_this": _plain(_call_str(widget, "whatsThis")),
        "status_tip": _call_str(widget, "statusTip"),
        "accessible_name": _call_str(widget, "accessibleName"),
        "accessible_description": _call_str(widget, "accessibleDescription"),
        "object_name": _call_str(widget, "objectName"),
        "tab_text": tabs,
        "properties": _dynamic_properties(widget),
        "class_name": type(widget).__name__,
        "style_sheet": _call_str(widget, "styleSheet"),
    }


def _class_matches(widget: Any, wanted: str) -> bool:
    name = wanted.casefold()
    return any(c.__name__.casefold() == name for c in type(widget).__mro__)


def _summary(ref: str, widget: Any) -> dict[str, Any]:
    g = widget.geometry()
    return {
        "ref": ref,
        "class": type(widget).__name__,
        "objectName": widget.objectName(),
        "text": _plain(_call_str(widget, "text")) or _call_str(widget, "title") or None,
        "geometry": {"x": g.x(), "y": g.y(), "width": g.width(), "height": g.height()},
        "visible": widget.isVisible(),
        "enabled": widget.isEnabled(),
    }


class SemanticFinder:
    """Finds widgets by description and registers them as refs."""

    def __init__(self, registry: RefRegistry) -> None:
        self._registry = registry

    # ----------------------------------------------------------------- search
    def _windows(self, visible_only: bool) -> list[Any]:
        app = QApplication.instance()
        if app is None:
            return []
        return [
            w for w in app.topLevelWidgets() if w.isWindow() and (w.isVisible() or not visible_only)
        ]

    def _search(
        self,
        *,
        describe: str,
        window_title: str,
        class_name: str,
        object_name: str,
        search_fields: list[str] | None,
        min_score: float,
        visible_only: bool,
        root_ref: str | None,
    ) -> list[tuple[Any, float, str, str]]:
        if not (describe or window_title or class_name or object_name):
            raise ValueError(
                "Give at least one of: describe, window_title, class_name, object_name"
            )
        only: set[str] | None = None
        if search_fields:
            only = set(search_fields)
            unknown = only - set(FIELD_WEIGHTS)
            if unknown:
                raise ValueError(
                    f"Unknown search_fields {sorted(unknown)}; valid: {sorted(FIELD_WEIGHTS)}"
                )
        if root_ref:
            root = self._registry.resolve_or_raise(root_ref)
            if not isinstance(root, QWidget):
                raise ValueError(f"Ref {root_ref} is not a QWidget")
            roots: list[Any] = [root]
        else:
            roots = self._windows(visible_only)

        windows_only = bool(window_title) and not (describe or class_name or object_name)
        hits: list[tuple[Any, float, str, str]] = []
        seen: set[int] = set()
        for win in roots:
            title = _call_str(win, "windowTitle")
            title_score = score(window_title, title) if window_title else 1.0
            if title_score < min_score:
                continue
            if windows_only:
                if id(win) not in seen:
                    seen.add(id(win))
                    hits.append((win, title_score, "window_title", title))
                continue
            for widget in [win, *win.findChildren(QWidget)]:
                if id(widget) in seen:
                    continue
                if visible_only and not widget.isVisible():
                    continue
                if class_name and not _class_matches(widget, class_name):
                    continue
                if object_name and not fnmatch.fnmatchcase(
                    _call_str(widget, "objectName"), object_name
                ):
                    continue
                seen.add(id(widget))
                if describe:
                    match = best_match(describe, widget_fields(widget), only=only)
                    if match is None or match.score < min_score:
                        continue
                    hits.append((widget, match.score, match.field, match.value))
                else:
                    hits.append((widget, 1.0, "", ""))
        hits.sort(key=lambda h: (-h[1], not h[0].isVisible()))
        return hits

    def find(
        self,
        describe: str = "",
        window_title: str = "",
        class_name: str = "",
        object_name: str = "",
        search_fields: list[str] | None = None,
        min_score: float = 0.5,
        visible_only: bool = True,
        max_results: int = 20,
        root_ref: str | None = None,
    ) -> dict[str, Any]:
        """Ranked matches (best first), each with a ref and why it matched."""
        hits = self._search(
            describe=describe,
            window_title=window_title,
            class_name=class_name,
            object_name=object_name,
            search_fields=search_fields,
            min_score=min_score,
            visible_only=visible_only,
            root_ref=root_ref,
        )[:max_results]
        out = []
        for widget, sc, field, value in hits:
            item = _summary(self._registry.register(widget, prefix="w"), widget)
            item["score"] = round(sc, 3)
            if field:
                item["matched_field"] = field
                item["matched_value"] = value[:120]
            out.append(item)
        return {"widgets": out, "count": len(out)}

    # ----------------------------------------------------------------- target
    def expand(self, widget: Any, mode: str) -> tuple[Any, str]:
        """Choose what to capture relative to a match. Returns ``(target, note)``."""
        if mode not in EXPAND_MODES:
            raise ValueError(f"expand must be one of {EXPAND_MODES}")
        if mode == "window":
            return widget.window(), ""
        if mode == "parent":
            parent = widget.parentWidget()
            if parent is None:
                return widget, "Matched widget has no parent; captured it directly."
            return parent, ""
        if mode == "buddy":
            buddy = widget.buddy() if hasattr(widget, "buddy") else None
            if buddy is None:
                return widget, "Matched widget has no buddy; captured it directly."
            return buddy, ""
        if mode == "auto" and isinstance(widget, QLabel) and not widget.isWindow():
            parent = widget.parentWidget()
            if parent is not None:
                return parent, (
                    f"Matched a label; captured its parent {type(parent).__name__}. "
                    "Use expand='self' for the label alone or 'buddy' for its field."
                )
        return widget, ""

    def find_target(
        self,
        describe: str = "",
        window_title: str = "",
        class_name: str = "",
        object_name: str = "",
        search_fields: list[str] | None = None,
        min_score: float = 0.5,
        visible_only: bool = True,
        expand: str = "auto",
        index: int = 0,
        root_ref: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a description to the one widget to capture (plus alternatives)."""
        hits = self._search(
            describe=describe,
            window_title=window_title,
            class_name=class_name,
            object_name=object_name,
            search_fields=search_fields,
            min_score=min_score,
            visible_only=visible_only,
            root_ref=root_ref,
        )
        if not hits:
            raise ValueError(
                "No widget matched. Try qt_find_widget / qt_snapshot to see what is on "
                "screen, lower min_score, or set visible_only=false."
            )
        if not 0 <= index < len(hits):
            raise ValueError(f"index {index} is out of range; {len(hits)} widget(s) matched.")
        widget, sc, field, value = hits[index]
        target, note = self.expand(widget, expand)
        others = [h for i, h in enumerate(hits) if i != index]
        if others and others[0][1] >= sc - 0.05:
            note = (note + " " if note else "") + (
                f"{len(hits)} similar matches; pass index=N or a narrower description."
            )
        reg = self._registry
        matched = _summary(reg.register(widget, prefix="w"), widget)
        matched.update(score=round(sc, 3), matched_field=field, matched_value=value[:120])
        return {
            "ref": reg.register(target, prefix="w"),
            "target": _summary(reg.register(target, prefix="w"), target),
            "matched": matched,
            "note": note.strip(),
            "alternatives": [
                {**_summary(reg.register(w, prefix="w"), w), "score": round(s, 3)}
                for w, s, _f, _v in others[:4]
            ],
        }
