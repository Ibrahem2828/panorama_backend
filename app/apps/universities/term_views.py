"""Dashboard + app endpoints for the current term and the end-of-term workflow (see ``terms.py``)."""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import serializers, status
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageAcademic
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.idempotency import run_idempotent
from apps.common.negotiation import IgnoreClientContentNegotiation
from apps.common.responses import success_response

from . import terms
from .models import TermRollover


class CurrentTermWriteSerializer(serializers.Serializer):
    semester = serializers.IntegerField(min_value=1)
    label = serializers.CharField(max_length=100, allow_blank=True, required=False, default="")


class EndTermSerializer(serializers.Serializer):
    confirm = serializers.BooleanField()
    next_semester = serializers.IntegerField(min_value=1)
    label = serializers.CharField(max_length=100, allow_blank=True, required=False, default="")
    disable_whatsapp_links = serializers.BooleanField(required=False, default=True)

    def validate_confirm(self, value):
        if value is not True:
            raise serializers.ValidationError("Confirm the end of the term explicitly (confirm: true).")
        return value


class CurrentTermView(APIView):
    """``GET /academic/current-term/``: the semester the app shows first, plus "the other" semester."""

    @extend_schema(tags=["Academic"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return success_response(data=terms.current_term_payload(), request=request)


class DashboardCurrentTermView(APIView):
    permission_classes = [CanManageAcademic]
    serializer_class = CurrentTermWriteSerializer

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return success_response(data=terms.current_term_payload(), request=request)

    @extend_schema(tags=["Dashboard"], request=CurrentTermWriteSerializer, responses={200: OpenApiTypes.OBJECT})
    def put(self, request):
        serializer = CurrentTermWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        terms.set_current_term(
            semester_id=serializer.validated_data["semester"],
            label=serializer.validated_data["label"],
            actor=request.user,
            request=request,
        )
        return success_response(
            data=terms.current_term_payload(), message="Current term updated", request=request, code="CURRENT_TERM_SET"
        )


class EndTermPreviewView(APIView):
    permission_classes = [CanManageAcademic]
    serializer_class = serializers.Serializer

    @extend_schema(tags=["Dashboard"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        return success_response(data=terms.preview_end_term(), request=request)


def _rollover_dict(rollover: TermRollover, latest_id: int | None) -> dict:
    performer = rollover.performed_by
    return {
        "id": rollover.pk,
        "created_at": rollover.created_at,
        "performed_by": {"id": performer.pk, "full_name": performer.full_name} if performer else None,
        "from_semester": rollover.from_semester_id,
        "to_semester": rollover.to_semester_id,
        "from_label": rollover.from_label,
        "to_label": rollover.to_label,
        "disable_whatsapp_links": rollover.disable_whatsapp_links,
        "memberships_removed": rollover.memberships_removed,
        "students_affected": rollover.students_affected,
        "groups_count": len(rollover.snapshot or []),
        "is_undone": rollover.undone_at is not None,
        "undone_at": rollover.undone_at,
        "can_undo": rollover.undone_at is None and rollover.pk == latest_id,
    }


class EndTermView(APIView):
    permission_classes = [CanManageAcademic]
    serializer_class = EndTermSerializer

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        rollovers = list(
            TermRollover.objects.filter(is_deleted=False).select_related("performed_by").order_by("-created_at", "-id")[
                :100
            ]
        )
        latest = rollovers[0].pk if rollovers else None
        return success_response(data=[_rollover_dict(item, latest) for item in rollovers], request=request)

    @extend_schema(tags=["Dashboard"], request=EndTermSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        def write():
            serializer = EndTermSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            rollover = terms.end_term(
                actor=request.user,
                next_semester_id=serializer.validated_data["next_semester"],
                label=serializer.validated_data["label"],
                disable_whatsapp_links=serializer.validated_data["disable_whatsapp_links"],
                request=request,
            )
            return success_response(
                data={
                    "rollover_id": rollover.pk,
                    "removed": rollover.memberships_removed,
                    "students": rollover.students_affected,
                    "groups": rollover.snapshot,
                },
                message="Term ended",
                request=request,
                code="TERM_ENDED",
            )

        return run_idempotent(request, "academic-end-term", write)


class WhatsAppRemovalListView(APIView):
    """JSON by default; ``?format=csv`` for a UTF-8 (BOM) spreadsheet. Phone numbers: every read is audited."""

    permission_classes = [CanManageAcademic]
    content_negotiation_class = IgnoreClientContentNegotiation

    @extend_schema(
        tags=["Dashboard"],
        parameters=[OpenApiParameter("format", str, enum=["json", "csv"], required=False)],
        responses={(200, "application/json"): OpenApiTypes.OBJECT, (200, "text/csv"): OpenApiTypes.BINARY},
    )
    def get(self, request, rollover_id: int):
        rollover = get_object_or_404(TermRollover, pk=rollover_id, is_deleted=False)
        data = terms.whatsapp_removal_list(rollover)
        wants_csv = request.query_params.get("format", "").lower() == "csv"
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.TERM_WHATSAPP_LIST_EXPORTED,
            target=rollover,
            new_value={"format": "csv" if wants_csv else "json", "groups": len(data["groups"])},
            request=request,
        )
        if not wants_csv:
            return success_response(data=data, request=request)
        response = HttpResponse(terms.removal_list_csv(data), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="whatsapp-removals-{rollover.pk}.csv"'
        return response


class UndoEndTermView(APIView):
    permission_classes = [CanManageAcademic]
    serializer_class = serializers.Serializer

    @extend_schema(tags=["Dashboard"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, rollover_id: int):
        def write():
            result = terms.undo_end_term(rollover_id, actor=request.user, request=request)
            return success_response(
                data=result,
                message="End of term undone",
                status_code=status.HTTP_200_OK,
                request=request,
                code="TERM_UNDONE",
            )

        return run_idempotent(request, f"academic-end-term-undo:{rollover_id}", write)
