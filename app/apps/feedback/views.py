from __future__ import annotations

from django.db.models import Count
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageFeedback
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.throttles import FeedbackRateThrottle
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import AppFeedback, FeedbackKind, FeedbackPromptPolicy, FeedbackStatus
from .serializers import (
    AppFeedbackSerializer,
    DashboardFeedbackSerializer,
    DashboardFeedbackPromptPolicySerializer,
    FeedbackPromptEventSerializer,
    FeedbackPromptPolicySerializer,
    PublicSuggestionSerializer,
    FeedbackWorkflowSerializer,
)
from .services import FeedbackPromptService, FeedbackService, FeedbackWorkflowService


class FeedbackPromptEligibilityView(APIView):
    @extend_schema(tags=["Feedback"], responses={200: FeedbackPromptPolicySerializer})
    def get(self, request):
        context = request.query_params.get("context", "app")
        action_key = request.query_params.get("action_key", "")[:100]
        app_version = request.query_params.get("app_version", "")[:32]
        policy = FeedbackPromptService.eligible(request.user, context, action_key, app_version)
        return success_response(
            data={
                "should_prompt": bool(policy),
                "policy": FeedbackPromptPolicySerializer(policy).data if policy else None,
            },
            request=request,
        )


class FeedbackPromptEventView(APIView):
    serializer_class = FeedbackPromptEventSerializer

    @extend_schema(tags=["Feedback"], request=FeedbackPromptEventSerializer)
    def post(self, request):
        serializer = FeedbackPromptEventSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(message="Prompt event recorded", request=request, code="FEEDBACK_PROMPT_EVENT_RECORDED")


class FeedbackSubmitView(APIView):
    throttle_classes = [FeedbackRateThrottle]
    serializer_class = AppFeedbackSerializer

    @extend_schema(tags=["Feedback"], request=AppFeedbackSerializer, responses={201: AppFeedbackSerializer})
    def post(self, request):
        serializer = AppFeedbackSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        feedback = serializer.save()
        return success_response(
            data=AppFeedbackSerializer(feedback, context={"request": request}).data,
            message="Thank you. Your feedback was recorded.",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="FEEDBACK_SUBMITTED",
        )


class MyFeedbackViewSet(StandardReadOnlyModelViewSet):
    serializer_class = AppFeedbackSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["kind", "context", "status", "rating"]
    ordering_fields = ["created_at", "rating", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AppFeedback.objects.none()
        return AppFeedback.objects.filter(user=self.request.user, is_deleted=False).annotate(votes_count=Count("votes"))


class PublicSuggestionViewSet(StandardReadOnlyModelViewSet):
    serializer_class = PublicSuggestionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["context", "status"]
    search_fields = ["title", "suggestion"]
    ordering_fields = ["created_at", "votes_count"]
    ordering = ["-votes_count", "-created_at"]

    def get_queryset(self):
        return (
            AppFeedback.objects.filter(
                kind=FeedbackKind.SUGGESTION,
                status__in=[FeedbackStatus.PLANNED, FeedbackStatus.IN_PROGRESS, FeedbackStatus.RESOLVED],
                is_deleted=False,
            )
            .exclude(suggestion="")
            .annotate(votes_count=Count("votes"))
        )


class FeedbackVoteView(APIView):
    @extend_schema(tags=["Feedback"], request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        feedback = get_object_or_404(AppFeedback, pk=pk, is_deleted=False)
        voted, votes_count = FeedbackService.toggle_vote(request.user, feedback)
        return success_response(
            data={"voted": voted, "votes_count": votes_count},
            message="Suggestion vote updated",
            request=request,
            code="FEEDBACK_VOTE_UPDATED",
        )


class DashboardFeedbackViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [CanManageFeedback]
    serializer_class = DashboardFeedbackSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["kind", "context", "action_key", "status", "priority", "rating", "assigned_to", "platform", "app_version"]
    search_fields = ["title", "comment", "suggestion", "user__full_name", "user__email"]
    ordering_fields = ["created_at", "rating", "priority", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return (
            AppFeedback.objects.filter(is_deleted=False)
            .select_related("user", "assigned_to")
            .annotate(votes_count=Count("votes"))
        )


class DashboardFeedbackWorkflowView(APIView):
    permission_classes = [CanManageFeedback]
    serializer_class = FeedbackWorkflowSerializer

    @extend_schema(tags=["Dashboard"], request=FeedbackWorkflowSerializer, responses={200: DashboardFeedbackSerializer})
    def patch(self, request, pk: int):
        feedback = get_object_or_404(AppFeedback, pk=pk, is_deleted=False)
        serializer = FeedbackWorkflowSerializer(data=request.data, context={"feedback": feedback})
        serializer.is_valid(raise_exception=True)
        feedback = FeedbackWorkflowService.update(
            feedback,
            request.user,
            serializer.validated_data,
            request=request,
        )
        return success_response(
            data=DashboardFeedbackSerializer(feedback, context={"request": request}).data,
            message="Feedback workflow updated",
            request=request,
            code="FEEDBACK_WORKFLOW_UPDATED",
        )


class DashboardFeedbackAnalyticsView(APIView):
    permission_classes = [CanManageFeedback]

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        queryset = AppFeedback.objects.filter(is_deleted=False)
        for field in ("context", "kind", "status", "platform", "app_version"):
            value = request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return success_response(data=FeedbackService.analytics(queryset), request=request)


class DashboardFeedbackPromptPolicyViewSet(StandardModelViewSet):
    permission_classes = [CanManageFeedback]
    serializer_class = DashboardFeedbackPromptPolicySerializer
    pagination_class = None

    def get_queryset(self):
        return FeedbackPromptPolicy.objects.filter(is_deleted=False)
