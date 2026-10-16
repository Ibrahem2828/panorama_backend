"""Validated, idempotent import of production reference data (see ``import_reference_data``).

The file is validated completely (schema, references, duplicates, prices, dates) before anything is written, then
applied inside ONE transaction by upserting on natural keys. Nothing is ever deleted and no user is ever created.
Every problem is reported with a JSON path such as ``universities[0].faculties[2].code``.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Model

from apps.accounts.student_number import FACULTY_CODE_LABELS
from apps.printing.models import (
    PrintBinding,
    PrintBindingPrice,
    PrintColorMode,
    PrintPaperSize,
    PrintPickupLocation,
    PrintPricingRule,
    PrintSides,
)
from apps.product.models import PrivacyPolicyVersion, TermsVersion
from apps.universities.models import AcademicYear, Faculty, Major, Semester, Subject, University

SCHEMA_VERSION = 1
MAX_MONEY = Decimal("9999999999.99")  # DecimalField(max_digits=12, decimal_places=2)
_url_validator = URLValidator(schemes=["https"])


@dataclass(frozen=True)
class Spec:
    kind: str  # str | bool | int | decimal | datetime | url | choice | ref
    required: bool = False
    default: Any = None
    max_length: int | None = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    nullable: bool = False


YEAR_FIELDS = {
    "name": Spec("str", True, max_length=100),
    "order": Spec("int", True, minimum=1, maximum=32767),
    "is_active": Spec("bool", default=True),
}
UNIVERSITY_FIELDS = {
    "code": Spec("str", True, max_length=32),
    "name": Spec("str", True, max_length=255),
    "description": Spec("str", default="", max_length=5000),
    "is_active": Spec("bool", default=True),
}
FACULTY_FIELDS = {
    "code": Spec("choice", True, choices=tuple(FACULTY_CODE_LABELS)),
    "name": Spec("str", True, max_length=255),
    "is_active": Spec("bool", default=True),
}
MAJOR_FIELDS = {
    "code": Spec("str", default="", max_length=32),
    "name": Spec("str", True, max_length=255),
    "is_active": Spec("bool", default=True),
}
SUBJECT_FIELDS = {
    "code": Spec("str", default="", max_length=32),
    "name": Spec("str", True, max_length=255),
    "academic_year": Spec("str", True, max_length=100),  # reference by AcademicYear.name
    "semester": Spec("str", True, max_length=100),  # reference by Semester.name
    "description": Spec("str", default="", max_length=5000),
    "is_active": Spec("bool", default=True),
}
FACULTY_SUBJECT_FIELDS = {
    **SUBJECT_FIELDS,
    # Optional specialization tag: code or name of a major of the same faculty. Omitted/null = no specialization.
    "major": Spec("str", default=None, max_length=255, nullable=True),
}
RULE_FIELDS = {
    "name": Spec("str", True, max_length=150),
    "color_mode": Spec("choice", True, choices=tuple(PrintColorMode.values)),
    "paper_size": Spec("choice", True, choices=tuple(PrintPaperSize.values)),
    "sides": Spec("choice", True, choices=tuple(PrintSides.values)),
    "price_per_sheet": Spec("decimal", True),
    "setup_fee": Spec("decimal", default=Decimal("0.00")),
    "currency": Spec("str", default="SYP", max_length=8),
    "is_active": Spec("bool", default=True),
    "effective_from": Spec("datetime", True),
    "effective_to": Spec("datetime", default=None, nullable=True),
}
BINDING_FIELDS = {
    "binding": Spec("choice", True, choices=tuple(PrintBinding.values)),
    "price_per_copy": Spec("decimal", True),
    "currency": Spec("str", default="SYP", max_length=8),
    "is_active": Spec("bool", default=True),
}
PICKUP_FIELDS = {
    "name": Spec("str", True, max_length=150),
    "address": Spec("str", default="", max_length=2000),
    "instructions": Spec("str", default="", max_length=2000),
    "is_active": Spec("bool", default=True),
    "sort_order": Spec("int", default=0, minimum=0, maximum=32767),
}
POLICY_FIELDS = {
    "version": Spec("str", True, max_length=64),
    "content_url": Spec("url", True),
    "is_active": Spec("bool", default=True),
    "requires_reacceptance": Spec("bool", default=False),
    "published_at": Spec("datetime", True),
}

TOP_LEVEL_KEYS = {
    "schema_version",
    "academic_years",
    "semesters",
    "universities",
    "print_pricing_rules",
    "binding_prices",
    "pickup_locations",
    "terms_versions",
    "privacy_versions",
}


class ReferenceDataError(Exception):
    """Raised with a list of ``path: message`` strings when the file or the database state is not acceptable."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class ImportReport:
    counts: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    warnings: list[str] = field(default_factory=list)

    def bump(self, entity: str, state: str) -> None:
        self.counts[entity][state] += 1

    def lines(self) -> list[str]:
        rows = []
        for entity in sorted(self.counts):
            c = self.counts[entity]
            rows.append(
                f"{entity:<22} created={c.get('created', 0):<4} updated={c.get('updated', 0):<4} "
                f"unchanged={c.get('unchanged', 0)}"
            )
        return rows

    @property
    def total_changes(self) -> int:
        return sum(c.get("created", 0) + c.get("updated", 0) for c in self.counts.values())


# --------------------------------------------------------------------------------------------------------------
# Parsing and validation (pure: no database access)
# --------------------------------------------------------------------------------------------------------------


def load_json_file(path: str | Path) -> Any:
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ReferenceDataError([f"$: cannot read file {path}: {exc.strerror or exc}"]) from exc
    except UnicodeDecodeError as exc:
        raise ReferenceDataError(["$: file is not valid UTF-8"]) from exc
    try:
        return json.loads(raw, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ReferenceDataError([f"$: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"]) from exc


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = datetime.combine(date.fromisoformat(text), time.min)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _convert(value: Any, spec: Spec, path: str, errors: list[str]) -> Any:
    kind = spec.kind
    if value is None:
        if spec.nullable:
            return None
        errors.append(f"{path}: must not be null")
        return None
    if kind in {"str", "ref", "choice", "url"}:
        if not isinstance(value, str):
            errors.append(f"{path}: must be a string")
            return None
        text = value.strip()
        if spec.required and not text:
            errors.append(f"{path}: must not be empty")
            return None
        if spec.max_length is not None and len(text) > spec.max_length:
            errors.append(f"{path}: longer than {spec.max_length} characters")
            return None
        if kind == "choice" and text not in spec.choices:
            errors.append(f"{path}: {text!r} is not one of {sorted(spec.choices)}")
            return None
        if kind == "url":
            try:
                _url_validator(text)
            except ValidationError:
                errors.append(f"{path}: must be a valid https:// URL")
                return None
        return text
    if kind == "bool":
        if not isinstance(value, bool):
            errors.append(f"{path}: must be true or false")
            return None
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: must be an integer")
            return None
        if spec.minimum is not None and value < spec.minimum:
            errors.append(f"{path}: must be >= {spec.minimum}")
            return None
        if spec.maximum is not None and value > spec.maximum:
            errors.append(f"{path}: must be <= {spec.maximum}")
            return None
        return value
    if kind == "decimal":
        if isinstance(value, bool) or not isinstance(value, int | str | Decimal):
            errors.append(f"{path}: must be a number (or a numeric string)")
            return None
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            errors.append(f"{path}: {value!r} is not a valid decimal number")
            return None
        if not number.is_finite():
            errors.append(f"{path}: must be a finite number")
            return None
        if number < 0:
            errors.append(f"{path}: must be >= 0 (got {number})")
            return None
        if number != number.quantize(Decimal("0.01")):
            errors.append(f"{path}: at most 2 decimal places are allowed (got {number})")
            return None
        if number > MAX_MONEY:
            errors.append(f"{path}: too large")
            return None
        return number.quantize(Decimal("0.01"))
    if kind == "datetime":
        if not isinstance(value, str):
            errors.append(f"{path}: must be an ISO 8601 date or datetime string")
            return None
        parsed = _parse_datetime(value)
        if parsed is None:
            errors.append(f"{path}: {value!r} is not a valid ISO 8601 date/datetime (e.g. 2026-01-31T00:00:00Z)")
        return parsed
    raise AssertionError(kind)  # pragma: no cover


def _record(
    raw: Any, specs: dict[str, Spec], path: str, errors: list[str], extra_keys: frozenset[str] = frozenset()
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        errors.append(f"{path}: must be an object")
        return {}
    out: dict[str, Any] = {}
    for key in raw:
        if key not in specs and key not in extra_keys:
            errors.append(f"{path}.{key}: unknown field")
    for name, spec in specs.items():
        if name not in raw:
            if spec.required:
                errors.append(f"{path}.{name}: required")
            out[name] = spec.default
            continue
        out[name] = _convert(raw[name], spec, f"{path}.{name}", errors)
    return out


def _list(raw: Any, path: str, errors: list[str]) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"{path}: must be a list")
        return []
    return raw


def _unique(records: list[dict[str, Any]], key: Any, label: str, base: str, errors: list[str]) -> None:
    seen: dict[Any, int] = {}
    for index, record in enumerate(records):
        value = key(record)
        if value is None or value == "":
            continue
        if value in seen:
            errors.append(f"{base}[{index}]: duplicate {label} {value!r} (first used at {base}[{seen[value]}])")
        else:
            seen[value] = index


@dataclass
class ParsedData:
    years: list[dict[str, Any]] = field(default_factory=list)
    semesters: list[dict[str, Any]] = field(default_factory=list)
    universities: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    pickups: list[dict[str, Any]] = field(default_factory=list)
    terms: list[dict[str, Any]] = field(default_factory=list)
    privacy: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_document(document: Any) -> ParsedData:
    """Validate the whole document; raise ``ReferenceDataError`` listing every problem found."""

    errors: list[str] = []
    data = ParsedData()
    if not isinstance(document, dict):
        raise ReferenceDataError(["$: the file must contain a JSON object"])
    for key in document:
        if key not in TOP_LEVEL_KEYS:
            errors.append(f"$.{key}: unknown top-level section")
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"$.schema_version: must be {SCHEMA_VERSION}")

    for i, raw in enumerate(_list(document.get("academic_years"), "academic_years", errors)):
        data.years.append(_record(raw, YEAR_FIELDS, f"academic_years[{i}]", errors))
    for i, raw in enumerate(_list(document.get("semesters"), "semesters", errors)):
        data.semesters.append(_record(raw, SEMESTER_FIELDS, f"semesters[{i}]", errors))
    _unique(data.years, lambda r: r["name"], "name", "academic_years", errors)
    _unique(data.years, lambda r: r["order"], "order", "academic_years", errors)
    _unique(data.semesters, lambda r: r["name"], "name", "semesters", errors)
    _unique(data.semesters, lambda r: r["order"], "order", "semesters", errors)

    _parse_universities(document, data, errors)
    _parse_printing(document, data, errors)
    _parse_policies(document, data, errors)

    sections = [data.years, data.semesters, data.universities, data.rules, data.bindings, data.pickups]
    if not any(sections) and not data.terms and not data.privacy:
        errors.append("$: the file does not contain any data section")
    if errors:
        raise ReferenceDataError(errors)
    return data


SEMESTER_FIELDS = YEAR_FIELDS


def _unique_subject_codes(faculty: dict[str, Any], fpath: str, errors: list[str]) -> None:
    """A subject code is unique per faculty, across the faculty-level and the nested per-major subjects."""

    seen: dict[str, str] = {}
    located = [(f"{fpath}.subjects[{i}]", r) for i, r in enumerate(faculty["subjects"])]
    for mi, major in enumerate(faculty["majors"]):
        located += [(f"{fpath}.majors[{mi}].subjects[{i}]", r) for i, r in enumerate(major["subjects"])]
    for where, record in located:
        code = (record.get("code") or "").upper()
        if not code:
            continue
        if code in seen:
            errors.append(f"{where}: duplicate subject code {code!r} (first used at {seen[code]})")
        else:
            seen[code] = where


def _parse_universities(document: dict[str, Any], data: ParsedData, errors: list[str]) -> None:
    for ui, raw_u in enumerate(_list(document.get("universities"), "universities", errors)):
        upath = f"universities[{ui}]"
        university = _record(raw_u, UNIVERSITY_FIELDS, upath, errors, frozenset({"faculties"}))
        university["faculties"] = []
        for fi, raw_f in enumerate(
            _list(raw_u.get("faculties") if isinstance(raw_u, dict) else None, f"{upath}.faculties", errors)
        ):
            fpath = f"{upath}.faculties[{fi}]"
            faculty = _record(raw_f, FACULTY_FIELDS, fpath, errors, frozenset({"majors", "subjects"}))
            faculty["majors"] = []
            faculty["subjects"] = []
            raw_majors = raw_f.get("majors") if isinstance(raw_f, dict) else None
            for mi, raw_m in enumerate(_list(raw_majors, f"{fpath}.majors", errors)):
                mpath = f"{fpath}.majors[{mi}]"
                major = _record(raw_m, MAJOR_FIELDS, mpath, errors, frozenset({"subjects"}))
                major["subjects"] = []
                raw_subjects = raw_m.get("subjects") if isinstance(raw_m, dict) else None
                for si, raw_s in enumerate(_list(raw_subjects, f"{mpath}.subjects", errors)):
                    spath = f"{mpath}.subjects[{si}]"
                    subject = _record(raw_s, SUBJECT_FIELDS, spath, errors)
                    major["subjects"].append(subject)
                _unique(
                    major["subjects"],
                    lambda r: (r["academic_year"], r["semester"], r["name"]),
                    "subject (year, semester, name)",
                    f"{mpath}.subjects",
                    errors,
                )
                faculty["majors"].append(major)
            raw_faculty_subjects = raw_f.get("subjects") if isinstance(raw_f, dict) else None
            for si, raw_s in enumerate(_list(raw_faculty_subjects, f"{fpath}.subjects", errors)):
                faculty["subjects"].append(_record(raw_s, FACULTY_SUBJECT_FIELDS, f"{fpath}.subjects[{si}]", errors))
            _unique(
                faculty["subjects"],
                lambda r: ((r["major"] or "").casefold(), r["academic_year"], r["semester"], r["name"]),
                "subject (major, year, semester, name)",
                f"{fpath}.subjects",
                errors,
            )
            _unique_subject_codes(faculty, fpath, errors)
            _unique(faculty["majors"], lambda r: r["name"], "major name", f"{fpath}.majors", errors)
            _unique(
                faculty["majors"], lambda r: (r["code"] or "").upper() or None, "major code", f"{fpath}.majors", errors
            )
            university["faculties"].append(faculty)
        _unique(university["faculties"], lambda r: r["code"], "faculty code", f"{upath}.faculties", errors)
        _unique(university["faculties"], lambda r: r["name"], "faculty name", f"{upath}.faculties", errors)
        data.universities.append(university)
    _unique(data.universities, lambda r: (r["code"] or "").upper() or None, "university code", "universities", errors)
    _unique(data.universities, lambda r: r["name"], "university name", "universities", errors)
    # apply_student_number_parse links a profile to the FIRST faculty with the student-number prefix.
    by_code: dict[str, list[str]] = defaultdict(list)
    for university in data.universities:
        for faculty in university["faculties"]:
            if faculty["code"]:
                by_code[faculty["code"]].append(university["code"] or "?")
    for code, owners in sorted(by_code.items()):
        if len(owners) > 1:
            data.warnings.append(
                f"faculty code {code!r} is used by several universities ({', '.join(owners)}): student-number "
                "auto-linking resolves to the first match only"
            )


def _parse_printing(document: dict[str, Any], data: ParsedData, errors: list[str]) -> None:
    for i, raw in enumerate(_list(document.get("print_pricing_rules"), "print_pricing_rules", errors)):
        path = f"print_pricing_rules[{i}]"
        rule = _record(raw, RULE_FIELDS, path, errors)
        start, end = rule.get("effective_from"), rule.get("effective_to")
        if start and end and end <= start:
            errors.append(f"{path}.effective_to: must be after effective_from")
        data.rules.append(rule)
    _unique(data.rules, lambda r: r["name"], "rule name", "print_pricing_rules", errors)
    active = [(i, r) for i, r in enumerate(data.rules) if r.get("is_active") and r.get("effective_from")]
    for pos, (i, a) in enumerate(active):
        for j, b in active[pos + 1 :]:
            if (a["color_mode"], a["paper_size"], a["sides"]) != (b["color_mode"], b["paper_size"], b["sides"]):
                continue
            a_end = a["effective_to"] or datetime.max.replace(tzinfo=UTC)
            b_end = b["effective_to"] or datetime.max.replace(tzinfo=UTC)
            if a["effective_from"] < b_end and b["effective_from"] < a_end:
                errors.append(
                    f"print_pricing_rules[{j}]: active rule overlaps print_pricing_rules[{i}] "
                    "(same color_mode/paper_size/sides and overlapping effective dates)"
                )
    for i, raw in enumerate(_list(document.get("binding_prices"), "binding_prices", errors)):
        data.bindings.append(_record(raw, BINDING_FIELDS, f"binding_prices[{i}]", errors))
    _unique(data.bindings, lambda r: r["binding"], "binding", "binding_prices", errors)
    for i, raw in enumerate(_list(document.get("pickup_locations"), "pickup_locations", errors)):
        data.pickups.append(_record(raw, PICKUP_FIELDS, f"pickup_locations[{i}]", errors))
    _unique(data.pickups, lambda r: r["name"], "pickup location name", "pickup_locations", errors)


def _parse_policies(document: dict[str, Any], data: ParsedData, errors: list[str]) -> None:
    for section, target in (("terms_versions", data.terms), ("privacy_versions", data.privacy)):
        for i, raw in enumerate(_list(document.get(section), section, errors)):
            target.append(_record(raw, POLICY_FIELDS, f"{section}[{i}]", errors))
        _unique(target, lambda r: r["version"], "version", section, errors)
        if target and not any(r.get("is_active") for r in target):
            data.warnings.append(f"{section}: no entry is active, so no version will be served as current")


# --------------------------------------------------------------------------------------------------------------
# Applying (database)
# --------------------------------------------------------------------------------------------------------------


class _Applier:
    def __init__(self, data: ParsedData):
        self.data = data
        self.report = ImportReport(warnings=list(data.warnings))
        self.errors: list[str] = []

    def upsert(
        self, entity: str, model: type[Model], lookup: dict[str, Any], values: dict[str, Any], path: str
    ) -> Model | None:
        """Create or update one row on its natural key, inside a savepoint so one bad row cannot mask the others."""

        try:
            with transaction.atomic():
                matches = list(model._default_manager.filter(**lookup)[:2])
                if len(matches) > 1:
                    self.errors.append(f"{path}: several existing rows match {lookup}; resolve this manually")
                    return None
                values = {**values, "is_deleted": False, "deleted_at": None}
                if not matches:
                    obj = model(**lookup, **values)
                    obj.save()
                    self.report.bump(entity, "created")
                    return obj
                obj = matches[0]
                changed = [name for name, value in values.items() if getattr(obj, name) != value]
                if not changed:
                    self.report.bump(entity, "unchanged")
                    return obj
                for name in changed:
                    setattr(obj, name, values[name])
                obj.save()
                self.report.bump(entity, "updated")
                return obj
        except (IntegrityError, ValidationError) as exc:
            detail = getattr(exc, "message_dict", None) or str(exc)
            self.errors.append(f"{path}: conflicts with existing data ({detail})")
            return None

    def run(self) -> ImportReport:
        d = self.data
        years: dict[str, AcademicYear] = {}
        semesters: dict[str, Semester] = {}
        for i, rec in enumerate(d.years):
            obj = self.upsert(
                "academic_years",
                AcademicYear,
                {"name": rec["name"]},
                {"order": rec["order"], "is_active": rec["is_active"]},
                f"academic_years[{i}]",
            )
            if obj:
                years[rec["name"]] = obj  # type: ignore[assignment]
        for i, rec in enumerate(d.semesters):
            obj = self.upsert(
                "semesters",
                Semester,
                {"name": rec["name"]},
                {"order": rec["order"], "is_active": rec["is_active"]},
                f"semesters[{i}]",
            )
            if obj:
                semesters[rec["name"]] = obj  # type: ignore[assignment]
        self._universities(years, semesters)
        for i, rec in enumerate(d.rules):
            values = {k: v for k, v in rec.items() if k != "name"}
            self.upsert(
                "print_pricing_rules", PrintPricingRule, {"name": rec["name"]}, values, f"print_pricing_rules[{i}]"
            )
        for i, rec in enumerate(d.bindings):
            values = {k: v for k, v in rec.items() if k != "binding"}
            self.upsert(
                "binding_prices", PrintBindingPrice, {"binding": rec["binding"]}, values, f"binding_prices[{i}]"
            )
        for i, rec in enumerate(d.pickups):
            values = {k: v for k, v in rec.items() if k != "name"}
            self.upsert(
                "pickup_locations", PrintPickupLocation, {"name": rec["name"]}, values, f"pickup_locations[{i}]"
            )
        for section, model, records in (
            ("terms_versions", TermsVersion, d.terms),
            ("privacy_versions", PrivacyPolicyVersion, d.privacy),
        ):
            for i, rec in enumerate(records):
                values = {k: v for k, v in rec.items() if k != "version"}
                self.upsert(section, model, {"version": rec["version"]}, values, f"{section}[{i}]")
        return self.report

    def _lookup_ref(self, cache: dict[str, Any], model: type[Model], name: str) -> Any:
        if name in cache:
            return cache[name]
        return model._default_manager.filter(name=name, is_deleted=False).first()

    def _faculty_subjects(
        self,
        faculty: Faculty,
        raw: dict[str, Any],
        path: str,
        years: dict[str, AcademicYear],
        semesters: dict[str, Semester],
    ) -> None:
        """Faculty-level subjects (``faculties[].subjects[]``); ``major`` optionally tags one of the faculty's majors."""

        for si, s in enumerate(raw["subjects"]):
            spath = f"{path}[{si}]"
            major = None
            if s["major"]:
                major = self._resolve_major(faculty, s["major"], f"{spath}.major")
                if major is None:
                    continue
            year = self._lookup_ref(years, AcademicYear, s["academic_year"])
            semester = self._lookup_ref(semesters, Semester, s["semester"])
            if year is None:
                self.errors.append(f"{spath}.academic_year: unknown academic year {s['academic_year']!r}")
            if semester is None:
                self.errors.append(f"{spath}.semester: unknown semester {s['semester']!r}")
            if year is None or semester is None:
                continue
            self.upsert(
                "subjects",
                Subject,
                {"faculty": faculty, "major": major, "academic_year": year, "semester": semester, "name": s["name"]},
                {"code": (s["code"] or "").upper(), "description": s["description"], "is_active": s["is_active"]},
                spath,
            )

    def _resolve_major(self, faculty: Faculty, ref: str, path: str) -> Major | None:
        candidates = Major.objects.filter(faculty=faculty, is_deleted=False)
        major = candidates.filter(code=ref.upper()).first() or candidates.filter(name=ref).first()
        if major is None:
            self.errors.append(f"{path}: {ref!r} is not a major (code or name) of this faculty")
        return major

    def _universities(self, years: dict[str, AcademicYear], semesters: dict[str, Semester]) -> None:
        for ui, u in enumerate(self.data.universities):
            upath = f"universities[{ui}]"
            code = u["code"].upper()
            university = self.upsert(
                "universities",
                University,
                {"code": code},
                {"name": u["name"], "description": u["description"], "is_active": u["is_active"]},
                upath,
            )
            if university is None:
                continue
            for fi, f in enumerate(u["faculties"]):
                fpath = f"{upath}.faculties[{fi}]"
                faculty = self.upsert(
                    "faculties",
                    Faculty,
                    {"university": university, "code": f["code"]},
                    {"name": f["name"], "is_active": f["is_active"]},
                    fpath,
                )
                if faculty is None:
                    continue
                for mi, m in enumerate(f["majors"]):
                    mpath = f"{fpath}.majors[{mi}]"
                    lookup: dict[str, Any] = {"faculty": faculty}
                    values: dict[str, Any] = {"is_active": m["is_active"]}
                    if m["code"]:
                        lookup["code"] = m["code"].upper()
                        values["name"] = m["name"]
                    else:
                        lookup["name"] = m["name"]
                        values["code"] = ""
                    major = self.upsert("majors", Major, lookup, values, mpath)
                    if major is None:
                        continue
                    for si, s in enumerate(m["subjects"]):
                        spath = f"{mpath}.subjects[{si}]"
                        year = self._lookup_ref(years, AcademicYear, s["academic_year"])
                        semester = self._lookup_ref(semesters, Semester, s["semester"])
                        if year is None:
                            self.errors.append(f"{spath}.academic_year: unknown academic year {s['academic_year']!r}")
                        if semester is None:
                            self.errors.append(f"{spath}.semester: unknown semester {s['semester']!r}")
                        if year is None or semester is None:
                            continue
                        self.upsert(
                            "subjects",
                            Subject,
                            {
                                "faculty": faculty,
                                "major": major,
                                "academic_year": year,
                                "semester": semester,
                                "name": s["name"],
                            },
                            {
                                "code": (s["code"] or "").upper(),
                                "description": s["description"],
                                "is_active": s["is_active"],
                            },
                            spath,
                        )
                # After the majors so a faculty-level subject can tag a major created by this same file.
                self._faculty_subjects(cast(Faculty, faculty), f, f"{fpath}.subjects", years, semesters)


def import_reference_data(document: Any, *, dry_run: bool = False) -> ImportReport:
    """Validate then apply ``document`` in a single transaction (always rolled back for ``dry_run``)."""

    data = validate_document(document)
    with transaction.atomic():
        applier = _Applier(data)
        report = applier.run()
        if applier.errors:
            transaction.set_rollback(True)
            raise ReferenceDataError(applier.errors)
        if dry_run:
            transaction.set_rollback(True)
    return report
