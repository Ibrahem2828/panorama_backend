from __future__ import annotations

import copy
import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import User
from apps.printing.models import PrintBindingPrice, PrintPickupLocation, PrintPricingRule
from apps.product.models import PrivacyPolicyVersion, TermsVersion
from apps.universities.models import AcademicYear, Faculty, Major, Semester, Subject, University

EXAMPLE = Path(__file__).resolve().parents[3] / "docs" / "reference-data.example.json"

MODELS = [
    University,
    Faculty,
    Major,
    Subject,
    AcademicYear,
    Semester,
    PrintPricingRule,
    PrintBindingPrice,
    PrintPickupLocation,
    TermsVersion,
    PrivacyPolicyVersion,
]


def _counts() -> dict[str, int]:
    return {m.__name__: m._default_manager.count() for m in MODELS}


def _document() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _write(tmp_path: Path, document) -> Path:
    path = tmp_path / "ref.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _run(path: Path, *args: str) -> str:
    out = StringIO()
    call_command("import_reference_data", str(path), *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def _fails(path: Path) -> str:
    err = StringIO()
    with pytest.raises(CommandError):
        call_command("import_reference_data", str(path), stdout=StringIO(), stderr=err)
    return err.getvalue()


@pytest.mark.django_db
def test_example_file_is_valid_and_creates_everything():
    output = _run(EXAMPLE)
    assert "IMPORTED" in output
    assert University.objects.get(code="EXAMPLE").name == "EXAMPLE UNIVERSITY"
    assert sorted(Faculty.objects.values_list("code", flat=True)) == ["1", "2"]
    assert Major.objects.count() == 2
    assert Subject.objects.count() == 4
    assert Subject.objects.filter(major__isnull=True).count() == 1
    assert Subject.objects.filter(code="EX-G02", major__code="EX-MAJ-1").exists()
    subject = Subject.objects.get(code="EX-102")
    assert subject.academic_year.name == "EXAMPLE YEAR 1"
    assert subject.semester.name == "EXAMPLE SEMESTER 2"
    assert AcademicYear.objects.count() == 2
    assert Semester.objects.count() == 2
    assert PrintPricingRule.objects.count() == 2
    assert PrintBindingPrice.objects.count() == 4
    assert PrintPickupLocation.objects.count() == 1
    assert TermsVersion.objects.get().version == "EXAMPLE-0.0"
    assert PrivacyPolicyVersion.objects.count() == 1
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_reimport_is_a_noop():
    _run(EXAMPLE)

    def stamps():
        return {
            m.__name__: list(m._default_manager.order_by("pk").values_list("updated_at", flat=True)) for m in MODELS
        }

    before_counts, before_stamps = _counts(), stamps()
    output = _run(EXAMPLE)
    assert "created=0" in output
    assert "updated=0" in output
    assert "No changes" in output
    assert _counts() == before_counts
    assert stamps() == before_stamps


@pytest.mark.django_db
def test_reimport_updates_changed_values_without_deleting_anything(tmp_path):
    _run(EXAMPLE)
    doc = _document()
    doc["print_pricing_rules"][0]["price_per_sheet"] = "12.50"
    doc["universities"][0]["faculties"][0]["name"] = "EXAMPLE FACULTY ONE (renamed)"
    doc["pickup_locations"] = []  # omitted rows are left alone, never deleted
    output = _run(_write(tmp_path, doc))
    assert "updated=1" in output
    assert str(PrintPricingRule.objects.get(name="EXAMPLE BW A4 one-sided").price_per_sheet) == "12.50"
    assert Faculty.objects.get(code="1").name == "EXAMPLE FACULTY ONE (renamed)"
    assert PrintPickupLocation.objects.count() == 1


@pytest.mark.django_db
def test_dry_run_reports_but_changes_nothing():
    output = _run(EXAMPLE, "--dry-run")
    assert "DRY RUN" in output
    assert "created=1" in output
    assert all(count == 0 for count in _counts().values())


@pytest.mark.django_db
def test_dry_run_after_import_reports_unchanged():
    _run(EXAMPLE)
    output = _run(EXAMPLE, "--dry-run")
    assert "created=0" in output
    assert "unchanged=" in output


@pytest.mark.django_db
def test_bad_file_changes_nothing(tmp_path):
    doc = _document()
    doc["privacy_versions"][0]["content_url"] = "not a url"
    stderr = _fails(_write(tmp_path, doc))
    assert "privacy_versions[0].content_url" in stderr
    assert all(count == 0 for count in _counts().values())


@pytest.mark.django_db
def test_database_conflict_rolls_back_the_whole_import():
    # University.name is unique: the clash only surfaces while applying, after earlier sections were written.
    University.objects.create(code="OTHER", name="EXAMPLE UNIVERSITY")
    before = _counts()
    stderr = _fails(EXAMPLE)
    assert "universities[0]" in stderr
    assert _counts() == before


def _faculty(doc, i=0):
    return doc["universities"][0]["faculties"][i]


def _subject(doc):
    return _faculty(doc)["majors"][0]["subjects"][0]


def _dup_rule(doc):
    doc["print_pricing_rules"].append(copy.deepcopy(doc["print_pricing_rules"][0]))


def _dup_terms(doc):
    doc["terms_versions"].append(copy.deepcopy(doc["terms_versions"][0]))


CASES = [
    (lambda d: _faculty(d).update(code="9"), "universities[0].faculties[0].code"),
    (lambda d: _faculty(d, 1).update(code="1"), "universities[0].faculties[1]"),
    (lambda d: _subject(d).update(semester="NOPE"), "universities[0].faculties[0].majors[0].subjects[0].semester"),
    (lambda d: _subject(d).update(academic_year="NOPE"), "subjects[0].academic_year"),
    (lambda d: d["print_pricing_rules"][0].update(price_per_sheet="-1"), "print_pricing_rules[0].price_per_sheet"),
    (lambda d: d["print_pricing_rules"][0].update(setup_fee="1.234"), "print_pricing_rules[0].setup_fee"),
    (lambda d: d["print_pricing_rules"][0].update(price_per_sheet="abc"), "print_pricing_rules[0].price_per_sheet"),
    (lambda d: d["binding_prices"][1].update(price_per_copy=-5), "binding_prices[1].price_per_copy"),
    (lambda d: d["binding_prices"][1].update(binding="glue"), "binding_prices[1].binding"),
    (
        lambda d: d["print_pricing_rules"][1].update(effective_to="2098-01-01T00:00:00Z"),
        "print_pricing_rules[1].effective_to",
    ),
    (
        lambda d: d["print_pricing_rules"][0].update(effective_from="yesterday"),
        "print_pricing_rules[0].effective_from",
    ),
    (_dup_rule, "print_pricing_rules[2]"),
    (lambda d: d["academic_years"][1].update(order=1), "academic_years[1]"),
    (lambda d: d["terms_versions"][0].pop("published_at"), "terms_versions[0].published_at"),
    (_dup_terms, "terms_versions[1]"),
    (lambda d: d["pickup_locations"][0].update(unexpected=1), "pickup_locations[0].unexpected"),
    (lambda d: d.update(schema_version=2), "$.schema_version"),
    (lambda d: d.update(mystery=[]), "$.mystery"),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("mutate", "path"), CASES)
def test_validation_errors_name_the_json_path(tmp_path, mutate, path):
    doc = _document()
    mutate(doc)
    stderr = _fails(_write(tmp_path, doc))
    assert path in stderr, stderr
    assert all(count == 0 for count in _counts().values())


@pytest.mark.django_db
def test_unreadable_and_malformed_files_fail_cleanly(tmp_path):
    assert "cannot read file" in _fails(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert "invalid JSON" in _fails(bad)
    assert "must contain a JSON object" in _fails(_write(tmp_path, []))


@pytest.mark.django_db
def test_all_errors_are_reported_together(tmp_path):
    doc = _document()
    doc["print_pricing_rules"][0]["price_per_sheet"] = "-1"
    doc["binding_prices"][0]["price_per_copy"] = "-2"
    stderr = _fails(_write(tmp_path, doc))
    assert "print_pricing_rules[0].price_per_sheet" in stderr
    assert "binding_prices[0].price_per_copy" in stderr


@pytest.mark.django_db
def test_soft_deleted_rows_are_revived_not_duplicated():
    _run(EXAMPLE)
    University.objects.filter(code="EXAMPLE").update(is_deleted=True)
    _run(EXAMPLE)
    assert University.objects.filter(code="EXAMPLE").count() == 1
    assert University.objects.get(code="EXAMPLE").is_deleted is False


@pytest.mark.django_db
def test_shared_faculty_codes_across_universities_produce_a_warning(tmp_path):
    doc = _document()
    second = copy.deepcopy(doc["universities"][0])
    second.update(code="EXAMPLE2", name="EXAMPLE UNIVERSITY TWO")
    for faculty in second["faculties"]:
        for major in faculty["majors"]:
            major["subjects"] = []
    doc["universities"].append(second)
    output = _run(_write(tmp_path, doc))
    assert "WARNING faculty code" in output
