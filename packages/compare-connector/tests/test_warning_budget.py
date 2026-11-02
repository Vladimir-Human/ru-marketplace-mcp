"""A bounded warning list must say what it left out (R18).

The outside-in study found twelve distinct diagnostics of a typed source reply
arriving as ten, with no marker: the comparison looked like it had ten
problems when the source had reported twelve, and a warning that explains an
unhealthy answer could disappear behind informational notes.

These tests drive the public ``compare_prices`` with a typed native reply (the
real WB response model) and read both the per-source outcome and the answer.
"""

from __future__ import annotations

from types import SimpleNamespace

from compare_connector import server
from wb_connector.models_output import MetaOut, WbCardItem, WbSearchResponse

TWELVE_WARNINGS = [f"schema_warning_{index}: synthetic source diagnostic" for index in range(12)]


async def _compare_with_warnings(monkeypatch, warnings: list[str]):
    async def wb_search(**kwargs):
        return WbSearchResponse(
            items=[WbCardItem(nm_id=123, name="Kettle", price_rub=1000)],
            meta=MetaOut(source="wb_search", healthy=False, warnings=warnings),
        )

    monkeypatch.setitem(server.SOURCES, "wildberries", SimpleNamespace(wb_search=wb_search))
    return await server.compare_prices("Kettle", sources=["wildberries"])


async def test_a_cut_warning_list_names_the_number_it_suppressed(monkeypatch):
    """R18: the delivery stays bounded and says how much it dropped."""
    result = await _compare_with_warnings(monkeypatch, TWELVE_WARNINGS)

    outcome = result.source_outcomes[0]
    assert len(outcome.warnings) <= 10
    markers = [warning for warning in outcome.warnings if "truncat" in warning or "suppress" in warning]
    assert markers, outcome.warnings
    assert "3" in markers[0], markers[0]


async def test_the_answer_mentions_the_suppressed_warnings(monkeypatch):
    """The marker travels into the answer, not only into the outcome."""
    result = await _compare_with_warnings(monkeypatch, TWELVE_WARNINGS)

    assert any("truncat" in warning or "suppress" in warning for warning in result.warnings), result.warnings


async def test_a_warning_that_explains_an_unhealthy_answer_is_never_dropped(monkeypatch):
    """R18: real diagnostics fill the budget before informational notes."""
    warnings = [f"info: wb_dom: note {index}" for index in range(11)]
    warnings.append("parser_drift: the card page changed shape")

    result = await _compare_with_warnings(monkeypatch, warnings)

    outcome = result.source_outcomes[0]
    assert "parser_drift: the card page changed shape" in outcome.warnings, outcome.warnings
    assert len(outcome.warnings) <= 10


async def test_a_warning_list_that_fits_is_delivered_unchanged(monkeypatch):
    """The positive control: nothing is cut, so nothing is said about cutting."""
    warnings = ["price_kind: the listed price is a Wallet price"]

    result = await _compare_with_warnings(monkeypatch, warnings)

    outcome = result.source_outcomes[0]
    assert outcome.warnings == warnings
    assert not [warning for warning in outcome.warnings if "truncat" in warning]
