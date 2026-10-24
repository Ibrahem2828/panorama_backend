"""Saved audiences (\"verified students of Informatics, year 2\") so staff do not re-pick six fields for every upload."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import filters, serializers

from apps.accounts.permissions import CanManageFiles
from apps.common.viewsets import StandardModelViewSet

from .models import FileVisibilityTemplate


class FileVisibilityTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileVisibilityTemplate
        fields = [
            "id",
            "name",
            "visibility",
            "university",
            "faculty",
            "major",
            "academic_year",
            "semester",
            "subject",
            "group",
            "is_printable",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        merged = {**{f: getattr(self.instance, f, None) for f in self.Meta.fields if f != "id"}, **attrs}
        probe = FileVisibilityTemplate(**{key: value for key, value in merged.items() if key not in {"created_at"}})
        try:
            probe.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "error_dict") else exc.messages) from exc
        return attrs


class FileVisibilityTemplateViewSet(StandardModelViewSet):
    permission_classes = [CanManageFiles]
    serializer_class = FileVisibilityTemplateSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name"]
    ordering = ["name"]

    def get_queryset(self):
        return FileVisibilityTemplate.objects.filter(is_deleted=False)
