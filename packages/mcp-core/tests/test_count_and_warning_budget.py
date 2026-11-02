"""The shared count parser and the shared warning budget (R05, R18).

Both helpers live in mcp-core because both callers — a connector's own parser
and compare's adapter — must not drift apart. The connector suites prove them
through the public tools; these cases pin the rules themselves.

* ``coerce_count`` splits a known review label off and then parses the number
  strictly: a grouped count keeps its value, a sign, a range, two numbers and a
  magnitude form name no single count and are None.
* ``bound_warnings`` keeps a diagnostic list inside its budget and says how
  many diagnostics it suppressed; a diagnostic that explains an unhealthy
  answer is never dropped behind an ``info:`` note.
"""

from __future__ import annotations

import pytest
from mcp_core.resilience import bound_warnings, coerce_count


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("24 086 отзывов", 24086),
        ("1 200", 1200),
        ("1200", 1200),
        ("(15 374)", 15374),
        ("339 отзывов", 339),
        ("1 200 отзывов", 1200),
        ("1 200", 1200),
        ("5", 5),
        (42, 42),
    ],
)
def test_a_count_the_text_names_keeps_its_value(raw: object, expected: int) -> None:
    """The ordinary forms parse; the thousands separator is not a second number."""
    assert coerce_count(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "1.2K отзывов",
        "2 тыс.",
        "-3",
        "+5",
        "−5",
        "1 000–2 000",
        "1 999 2 999",
        "1200 300",
        "1 2000",
        "24 088₽",
        "нет",
        None,
        float("nan"),
        True,
    ],
)
def test_a_text_that_names_no_single_count_is_none(raw: object) -> None:
    """A magnitude form, a sign, a range, two numbers or a currency is no count."""
    assert coerce_count(raw) is None


def test_a_warning_list_that_fits_is_delivered_unchanged() -> None:
    """The positive control: nothing is cut, so nothing is said about cutting."""
    warnings = ["price_kind: the listed price is a Wallet price"]

    assert bound_warnings(warnings) == warnings


def test_a_cut_warning_list_names_the_number_it_suppressed() -> None:
    """The delivery stays bounded and says how much it dropped."""
    warnings = [f"schema_warning_{index}: synthetic diagnostic" for index in range(12)]

    delivered = bound_warnings(warnings)

    assert len(delivered) <= 10
    assert "3" in delivered[-1]
    assert "truncated" in delivered[-1]


def test_a_warning_that_explains_an_unhealthy_answer_is_never_dropped() -> None:
    """Real diagnostics fill the budget before informational notes."""
    warnings = [f"info: wb_dom: note {index}" for index in range(11)]
    warnings.append("parser_drift: the card page changed shape")

    delivered = bound_warnings(warnings)

    assert "parser_drift: the card page changed shape" in delivered
    assert len(delivered) <= 10


def test_the_truncation_marker_does_not_flip_health() -> None:
    """Truncation is a fact about the delivery, not a doubt about the data."""
    from mcp_core.resilience import health_warnings

    warnings = [f"schema_warning_{index}: synthetic diagnostic" for index in range(12)]

    delivered = bound_warnings(warnings)

    # The marker itself is an informational note, so the verdict (doubt or no
    # doubt) is the same on the delivered list and on the full one.
    assert bool(health_warnings(delivered)) == bool(health_warnings(warnings))
    assert not [warning for warning in delivered if warning.startswith("info:") and "truncat" not in warning]
