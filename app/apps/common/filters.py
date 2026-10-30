"""Reusable, strict filter building blocks for the dashboard lists.

Everything here fails loudly: an unparsable boolean, id, date or choice yields HTTP 400 (django-filter turns an invalid
filter form into a ``ValidationError``) instead of being silently ignored, so a typo in the dashboard never shows an
unfiltered list that looks filtered.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

import django_filters
from django import forms
from django.db.models import Count, IntegerField, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

TRUE_VALUES = {"true", "1", "yes"}
FALSE_VALUES = {"false", "0", "no"}


class StrictBooleanField(forms.Field):
    """``true``/``false`` (also 1/0, case-insensitive); anything else is an error. Empty means "not filtered"."""

    def to_python(self, value):
        if value in (None, ""):
            return None
        text = str(value).strip().lower()
        if text in TRUE_VALUES:
            return True
        if text in FALSE_VALUES:
            return False
        raise forms.ValidationError("Enter true or false.", code="invalid")


@extend_schema_field(OpenApiTypes.BOOL)
class StrictBooleanFilter(django_filters.Filter):
    field_class = StrictBooleanField


@extend_schema_field(OpenApiTypes.INT)
class StrictIntegerFilter(django_filters.Filter):
    """A plain integer id (``?faculty=3``); a non-integer is a 400, an id that matches nothing simply yields no rows."""

    field_class = forms.IntegerField


@extend_schema_field(OpenApiTypes.INT)
class PositiveIntegerFilter(django_filters.Filter):
    field_class = forms.IntegerField

    def __init__(self, *args: Any, min_value: int = 0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.extra["min_value"] = min_value


class IsoDateField(forms.DateField):
    """``YYYY-MM-DD`` only."""

    input_formats = ["%Y-%m-%d"]

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("input_formats", ["%Y-%m-%d"])
        super().__init__(**kwargs)


def _day_start(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


@extend_schema_field(OpenApiTypes.DATE)
class DateBoundFilter(django_filters.Filter):
    """Inclusive calendar-day bound on a datetime column.

    ``bound="after"`` keeps rows at or after 00:00 of the given day, ``bound="before"`` keeps rows up to the end of that
    day (``< 00:00`` of the next day). Comparing the raw datetime keeps the column's index usable.
    """

    field_class = IsoDateField

    def __init__(self, *args: Any, bound: str, **kwargs: Any) -> None:
        if bound not in {"after", "before"}:
            raise ValueError("bound must be 'after' or 'before'")
        super().__init__(*args, **kwargs)
        self.bound = bound

    def filter(self, qs: QuerySet, value: date | None) -> QuerySet:
        if value is None:
            return qs
        if self.bound == "after":
            return qs.filter(**{f"{self.field_name}__gte": _day_start(value)})
        return qs.filter(**{f"{self.field_name}__lt": _day_start(value + timedelta(days=1))})


class CsvChoiceField(forms.Field):
    """One value or a comma list, every item validated against ``choices``; the cleaned value is a list."""

    def __init__(self, *, choices: Iterable[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.allowed = {value for value, _label in choices}

    def to_python(self, value):
        if value in (None, ""):
            return []
        items = [item.strip() for item in str(value).split(",") if item.strip()]
        invalid = [item for item in items if item not in self.allowed]
        if invalid:
            raise forms.ValidationError(
                "Unknown value(s): %(bad)s. Allowed: %(allowed)s.",
                params={"bad": ", ".join(invalid), "allowed": ", ".join(sorted(self.allowed))},
                code="invalid_choice",
            )
        return items


@extend_schema_field(OpenApiTypes.STR)
class CsvChoiceFilter(django_filters.Filter):
    field_class = CsvChoiceField

    def __init__(self, *args: Any, choices: Iterable[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.extra["choices"] = [(value, value) for value in choices]

    def filter(self, qs: QuerySet, value: list[str]) -> QuerySet:
        if not value:
            return qs
        return qs.filter(**{f"{self.field_name}__in": value})


class MappedOrderingField(forms.Field):
    def __init__(self, *, allowed: Iterable[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.allowed = set(allowed)

    def to_python(self, value):
        if value in (None, ""):
            return []
        keys = [part.strip() for part in str(value).split(",") if part.strip()]
        bad = [key for key in keys if key.lstrip("-") not in self.allowed]
        if bad:
            raise forms.ValidationError(
                "Unsupported ordering: %(bad)s. Allowed: %(allowed)s (prefix - for descending).",
                params={"bad": ", ".join(bad), "allowed": ", ".join(sorted(self.allowed))},
                code="invalid_choice",
            )
        return keys


@extend_schema_field(OpenApiTypes.STR)
class MappedOrderingFilter(django_filters.Filter):
    """``?ordering=name,-created_at`` limited to an explicit whitelist.

    ``mapping`` maps the public key to a queryset field name or annotation. Nulls always sort last and ``-id``/``id`` is
    appended as a tiebreaker so pagination is stable.
    """

    field_class = MappedOrderingField

    def __init__(self, *args: Any, mapping: Mapping[str, str], default: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.mapping = dict(mapping)
        self.default = default
        self.extra["allowed"] = list(self.mapping)

    def filter(self, qs: QuerySet, value: list[str] | None) -> QuerySet:
        from django.db.models import F

        keys = value or ([part for part in self.default.split(",") if part] if self.default else [])
        if not keys:
            return qs
        expressions = []
        for key in keys:
            descending = key.startswith("-")
            target = F(self.mapping[key.lstrip("-")])
            expressions.append(target.desc(nulls_last=True) if descending else target.asc(nulls_last=True))
        first = keys[0]
        expressions.append(F("id").desc() if first.startswith("-") else F("id").asc())
        return qs.order_by(*expressions)


def count_subquery(queryset: QuerySet, outer_field: str, *, group_field: str) -> Coalesce:
    """``COALESCE((SELECT COUNT(*) FROM queryset WHERE group_field = outer.pk), 0)``.

    Counting in correlated subqueries (instead of several ``Count(..., distinct=True)`` joins) keeps the list one query
    and avoids the join fan-out that multiplies rows when two to-many relations are aggregated together.
    """

    inner = (
        queryset.filter(**{group_field: OuterRef(outer_field)})
        .order_by()
        .values(group_field)
        .annotate(_n=Count("pk"))
        .values("_n")
    )
    return Coalesce(Subquery(inner, output_field=IntegerField()), 0)
