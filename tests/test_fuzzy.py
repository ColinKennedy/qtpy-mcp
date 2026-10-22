from __future__ import annotations

import pytest

from qtpy_mcp.probe.fuzzy import best_match, normalize, score


def test_normalize_strips_punctuation_and_camel_case() -> None:
    assert normalize("Name goes here:") == "name goes here"
    assert normalize("nameEdit") == "name edit"
    assert normalize("submit_button") == "submit button"


def test_exact_match_beats_everything() -> None:
    assert score("Name goes here:", "Name goes here:") == 1.0
    assert score("name goes here", "Name goes here:") == 1.0


def test_substring_scores_high_and_prefers_tighter_fit() -> None:
    tight = score("full name", "Your full name")
    loose = score("full name", "Your full name as it appears on the legal invoice")
    assert 0.85 < loose < tight < 1.0


def test_typos_still_match() -> None:
    assert score("Nmae goes here", "Name goes here:") > 0.7


def test_unrelated_text_scores_low() -> None:
    assert score("submit button", "Email address:") < 0.5


def test_empty_inputs_score_zero() -> None:
    assert score("", "x") == 0.0
    assert score("x", "") == 0.0
    assert score("!!!", "???") == 0.0


def test_best_match_picks_strongest_weighted_field() -> None:
    fields = {"text": "Save", "tooltip": "Save the profile", "style_sheet": "color: red"}
    match = best_match("save the profile", fields)
    assert match is not None
    assert match.field == "tooltip"


def test_best_match_weights_label_text_over_tooltip() -> None:
    fields = {"text": "Name goes here:", "tooltip": "Name goes here:"}
    match = best_match("Name goes here:", fields)
    assert match is not None
    assert match.field == "text"
    assert match.score == pytest.approx(1.0)


def test_best_match_respects_only_filter() -> None:
    fields = {"text": "Submit", "tooltip": "Save the profile"}
    weak = best_match("submit", fields, only={"tooltip"})
    assert weak is None or weak.score < 0.5  # below the default min_score
    found = best_match("save profile", fields, only={"tooltip"})
    assert found is not None
    assert found.field == "tooltip"


def test_style_sheet_is_searchable() -> None:
    fields = {"style_sheet": "QPushButton { background-color: #2d7d46; color: white; }"}
    match = best_match("background-color: #2d7d46", fields, only={"style_sheet"})
    assert match is not None
    assert match.score > 0.4
