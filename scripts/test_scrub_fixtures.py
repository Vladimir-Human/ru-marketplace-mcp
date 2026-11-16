"""The fixture scrubber: personal data out, fixed placeholders in.

R-FIXTURE-PERSONAL-DATA: a scanner over the committed fixtures and recordings
finds the values in the same keys change (a) scrubs at runtime — reviewer,
asker, private seller and agent names, phones, e-mails — and replaces them with
fixed placeholders of the same shape; check_provenance runs it as a gate, and
the same gate holds the private-material scrub: a recording's region field
holds only a placeholder, no file under packages/*/tests/, scripts/, docs/ or .grace/
carries a Windows user path, and no committed fixture or recording carries the
scraping profile's directory name.

The tests below are requirement-derived: every key kind is found and replaced,
a company seller and an agency agent are kept, the output is stable on a second
run, the check fails on a planted recording with a real region value and on a
planted Windows user path, and the masked preview never carries the whole
value. The repository's own fixtures are the positive control: they carry no
personal data once the scrub has run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_provenance as gate  # noqa: E402
import scrub_fixtures as scrub  # noqa: E402

WB_FIXTURE = Path("packages/wb-connector/tests/fixtures/reviews.json")
OZON_FIXTURE = Path("packages/ozon-connector/tests/fixtures/reviews.json")
YANDEX_FIXTURE = Path("packages/yandex-connector/tests/fixtures/card.html")
AVITO_FIXTURE = Path("packages/avito-connector/tests/fixtures/items.json")
CIAN_FIXTURE = Path("packages/cian-connector/tests/fixtures/card.json")
CONTACT_FIXTURE = Path("packages/cian-connector/tests/fixtures/contacts.json")


def _hit_kinds(hits: list[scrub.Hit]) -> list[str]:
    return [hit.kind for hit in hits]


# --- the keys: every kind is found and replaced ---------------------------


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_a_wb_review_author_is_found_and_replaced() -> None:
    """WB carries the reviewer's name under wbUserDetails.name."""
    payload = {"feedbacks": [{"productValuation": 5, "text": "хорошо", "wbUserDetails": {"name": "Фёдор Ф."}}]}

    hits = scrub.scan(WB_FIXTURE, payload)

    assert _hit_kinds(hits) == ["reviewer"]
    assert hits[0].key_path == "feedbacks[0].wbUserDetails.name"
    scrubbed, _ = scrub.scrub(WB_FIXTURE, payload)
    assert scrubbed["feedbacks"][0]["wbUserDetails"]["name"] == "Покупатель"


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_a_wb_question_asker_is_found_and_replaced() -> None:
    """A WB question carries the asker's name under the same raw key."""
    payload = {"questions": [{"id": "1", "text": "в наличии?", "answer": {}, "wbUserDetails": {"name": "Пётр П."}}]}

    hits = scrub.scan(WB_FIXTURE, payload)

    assert _hit_kinds(hits) == ["asker"]
    scrubbed, _ = scrub.scrub(WB_FIXTURE, payload)
    assert scrubbed["questions"][0]["wbUserDetails"]["name"] == "Покупатель"


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_an_ozon_review_author_is_found_and_replaced() -> None:
    """Ozon splits the author's name into firstName and lastName."""
    payload = {"reviews": [{"author": {"firstName": "Анна", "lastName": "П."}, "content": {"score": 5}}]}

    hits = scrub.scan(OZON_FIXTURE, payload)

    assert _hit_kinds(hits) == ["reviewer", "reviewer"]
    scrubbed, _ = scrub.scrub(OZON_FIXTURE, payload)
    author = scrubbed["reviews"][0]["author"]
    assert author["firstName"] == "Покупатель"
    assert author["lastName"] == ""


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_a_yandex_review_author_is_found_and_replaced() -> None:
    """Yandex carries the reviewer's name under author.nickname."""
    payload = {"collections": {"reviews": {"reviews": [{"author": {"nickname": "Ольга О."}, "rating": 5}]}}}

    hits = scrub.scan(Path("packages/yandex-connector/tests/fixtures/state.json"), payload)

    assert _hit_kinds(hits) == ["reviewer"]
    assert hits[0].key_path == "collections.reviews.reviews[0].author.nickname"
    scrubbed, _ = scrub.scrub(Path("packages/yandex-connector/tests/fixtures/state.json"), payload)
    assert scrubbed["collections"]["reviews"]["reviews"][0]["author"]["nickname"] == "Покупатель"


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_a_private_avito_seller_name_is_found_and_replaced() -> None:
    """A private seller's name is personal data; the shop's is not."""
    payload = {"items": [{"id": 1, "seller": {"name": "Иван Петров", "isCompany": False}}]}

    hits = scrub.scan(AVITO_FIXTURE, payload)

    assert _hit_kinds(hits) == ["seller"]
    scrubbed, _ = scrub.scrub(AVITO_FIXTURE, payload)
    assert scrubbed["items"][0]["seller"]["name"] == "Продавец"


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_a_company_avito_seller_is_kept() -> None:
    """A company seller's name stays: the marketplace says so."""
    payload = {"items": [{"id": 1, "seller": {"name": "Магазин Техники", "isCompany": True}}]}

    hits = scrub.scan(AVITO_FIXTURE, payload)

    assert hits == []
    scrubbed, _ = scrub.scrub(AVITO_FIXTURE, payload)
    assert scrubbed["items"][0]["seller"]["name"] == "Магазин Техники"


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_a_private_cian_agent_name_is_found_and_replaced() -> None:
    """A private agent's name is personal data."""
    payload = {
        "offerData": {"agent": {"name": "Иван Петров", "userType": "realtor_not_commerce", "accountType": "specialist"}}
    }

    hits = scrub.scan(CIAN_FIXTURE, payload)

    assert _hit_kinds(hits) == ["agent"]
    scrubbed, _ = scrub.scrub(CIAN_FIXTURE, payload)
    assert scrubbed["offerData"]["agent"]["name"] == "Агент"


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_an_agency_cian_agent_is_kept() -> None:
    """An agency's name stays: it is a company, not a person."""
    payload = {"offerData": {"agent": {"name": "Дом Недвижимости", "userType": "developer", "accountType": "agency"}}}

    hits = scrub.scan(CIAN_FIXTURE, payload)

    assert hits == []
    scrubbed, _ = scrub.scrub(CIAN_FIXTURE, payload)
    assert scrubbed["offerData"]["agent"]["name"] == "Дом Недвижимости"


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_a_phone_keeps_its_format_with_a_fixed_number() -> None:
    """A phone keeps its shape: the country prefix stays, the number is fixed."""
    payload = {"contacts": [{"phones": [{"countryCode": "+7", "number": "+7 (999) 123-45-67"}]}]}

    hits = scrub.scan(CONTACT_FIXTURE, payload)

    assert _hit_kinds(hits) == ["phone"]
    scrubbed, _ = scrub.scrub(CONTACT_FIXTURE, payload)
    assert scrubbed["contacts"][0]["phones"][0]["number"] == "+7 (900) 000-00-00"


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_an_email_becomes_the_fixed_address() -> None:
    """An e-mail address is replaced by one fixed address."""
    payload = {"contacts": [{"email": "seller@example.org"}]}

    hits = scrub.scan(CONTACT_FIXTURE, payload)

    assert _hit_kinds(hits) == ["email"]
    scrubbed, _ = scrub.scrub(CONTACT_FIXTURE, payload)
    assert scrubbed["contacts"][0]["email"] == "user@example.com"


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_scanner_finds_a_reviewer_in_a_copy_of_the_real_card_fixture() -> None:
    """The rule fires on the real capture's shape, not only on synthetic payloads."""
    source = REPO_ROOT / "packages/yandex-connector/tests/fixtures/card_washer.html"
    text = source.read_text(encoding="utf-8")
    planted = text.replace('"nickname": "Покупатель"', '"nickname": "Тест Тестов"', 1)
    assert planted != text

    hits = scrub.scan(Path("packages/yandex-connector/tests/fixtures/card.html"), planted)

    assert _hit_kinds(hits) == ["reviewer"]
    assert hits[0].value == "Тест Тестов"
    scrubbed, _ = scrub.scrub(Path("packages/yandex-connector/tests/fixtures/card.html"), planted)
    assert "Тест Тестов" not in scrubbed


# --- the properties the gate relies on ------------------------------------


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_output_is_stable_on_a_second_run() -> None:
    """The same input gives the same output, and a second run finds nothing."""
    payload = {
        "feedbacks": [{"wbUserDetails": {"name": "Фёдор Ф."}}],
        "contacts": [{"phones": [{"number": "+7 (999) 123-45-67"}], "email": "seller@example.org"}],
    }

    first, hits = scrub.scrub(WB_FIXTURE, payload)
    second, again = scrub.scrub(WB_FIXTURE, first)

    assert hits and again == []
    assert second == first


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_masked_preview_never_carries_the_whole_value() -> None:
    """The gate prints a preview, never the value."""
    for value in ("Фёдор Ф.", "Ольга О.", "+7 (999) 123-45-67", "seller@example.org", "ab", "Иван"):
        preview = scrub.mask(value)
        assert value not in preview
        assert "*" in preview


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_gate_line_names_the_file_and_the_key_path() -> None:
    """One line per hit: the file, the key path and a masked preview."""
    payload = {"feedbacks": [{"wbUserDetails": {"name": "Фёдор Ф."}}]}

    hits = scrub.scan(WB_FIXTURE, payload)
    line = hits[0].line(REPO_ROOT)

    assert WB_FIXTURE.as_posix() in line
    assert "feedbacks[0].wbUserDetails.name" in line
    assert "Фёдор Ф." not in line


# --- the private-material rule --------------------------------------------


def _planted_tree(root: Path) -> Path:
    """A temporary repository tree the private-material rule can read."""
    fixtures = root / "packages" / "demo-connector" / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    return fixtures


def _planted_avito_fixture(root: Path) -> Path:
    """A temporary fixture tree of a source whose keys the scanner reads."""
    fixtures = root / "packages" / "avito-connector" / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    return fixtures


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_check_fails_on_a_planted_recording_with_a_real_region(tmp_path: Path) -> None:
    """A recording's region field holds a placeholder, never a real region."""
    fixtures = _planted_tree(tmp_path)
    (fixtures / "capture.provenance.json").write_text(json.dumps({"region": "Москва"}), encoding="utf-8")

    problems = scrub.private_material_problems(tmp_path)

    assert any("region" in problem for problem in problems)


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_check_accepts_a_planted_recording_with_a_placeholder_region(tmp_path: Path) -> None:
    """The placeholder form is what a scrubbed recording carries."""
    fixtures = _planted_tree(tmp_path)
    (fixtures / "capture.provenance.json").write_text(json.dumps({"region": "R1"}), encoding="utf-8")

    assert scrub.private_material_problems(tmp_path) == []


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_check_fails_on_a_planted_windows_user_path(tmp_path: Path) -> None:
    """No file under the covered trees may carry a Windows user path."""
    tests = tmp_path / "packages" / "demo-connector" / "tests"
    tests.mkdir(parents=True)
    (tests / "leak.txt").write_text("saved to C:\\Users\\operator\\secret.txt", encoding="utf-8")

    problems = scrub.private_material_problems(tmp_path)

    assert any("user path" in problem for problem in problems)


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_check_fails_on_a_planted_profile_directory_name(tmp_path: Path) -> None:
    """Captured material may not carry the scraping profile's directory name."""
    fixtures = _planted_tree(tmp_path)
    (fixtures / "page.html").write_text("<p>%LOCALAPPDATA%\\Chrome-Scraping\\Default</p>", encoding="utf-8")

    problems = scrub.private_material_problems(tmp_path)

    assert problems == [
        "packages/demo-connector/tests/fixtures/page.html: the scraping profile directory name (Chrome-Scraping)"
    ]


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_published_default_profile_name_passes_in_docs(tmp_path: Path) -> None:
    """The default directory name is published; outside captured material it is no leak."""
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    (docs / "setup.md").write_text("The profile lives in %LOCALAPPDATA%\\Chrome-Scraping.", encoding="utf-8")

    assert scrub.private_material_problems(tmp_path) == []


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_check_covers_committed_grace_evidence(tmp_path: Path) -> None:
    """.grace/evidence/latest.json is committed; an interpreter path in it names the operator's account."""
    evidence = tmp_path / ".grace" / "evidence"
    evidence.mkdir(parents=True)
    interpreter = "C:" + "/Users/operator/w/x/.venv/Scripts/python.exe"  # split: this file is scanned too
    (evidence / "latest.json").write_text(json.dumps({"cmd": [interpreter]}), encoding="utf-8")

    problems = scrub.private_material_problems(tmp_path)

    assert any("user path" in problem for problem in problems)


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_a_real_user_path_next_to_an_allowed_fake_still_fails(tmp_path: Path) -> None:
    """The allowlist names exact fake paths, not whole files."""
    tests = tmp_path / "packages" / "mcp-core" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_process.py").write_text(
        'FAKE = r"C:\\Users\\op\\x"\nREAL = r"C:\\Users\\leaked\\x"\n', encoding="utf-8"
    )

    problems = scrub.private_material_problems(tmp_path)

    assert len(problems) == 1
    assert problems[0].startswith("packages/mcp-core/tests/test_process.py: a Windows user path")


# --- the gate itself -------------------------------------------------------


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_provenance_gate_fails_on_a_fixture_with_a_personal_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """check_provenance runs the scanner: a fixture with a name fails the gate."""
    fixtures = _planted_avito_fixture(tmp_path)
    (fixtures / "card.json").write_text(json.dumps({"seller": {"name": "Иван Петров"}}), encoding="utf-8")
    (fixtures / "card.provenance.json").write_text(json.dumps({"captured_at": "2026-11-16"}), encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "KNOWN_EXCEPTIONS", {}, raising=False)

    code = gate.main()
    output = capsys.readouterr().out

    assert code == 1
    assert "card.json" in output
    assert "Иван Петров" not in output


@pytest.mark.req("R-FIXTURE-PERSONAL-DATA")
def test_the_repository_fixtures_carry_no_personal_data() -> None:
    """The committed fixtures hold no personal name, phone or e-mail."""
    hits = []
    for path in scrub.fixture_files(REPO_ROOT):
        hits.extend(scrub.scan_file(path))

    assert hits == [], "\n".join(hit.line(REPO_ROOT) for hit in hits)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
