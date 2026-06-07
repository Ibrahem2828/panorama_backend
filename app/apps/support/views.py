from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminOrITSupport
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import SupportTicket
from .serializers import (
    SupportTicketAddMessageSerializer,
    SupportTicketAssignSerializer,
    SupportTicketCreateSerializer,
    SupportTicketPrioritySerializer,
    SupportTicketSerializer,
    SupportTicketStatusSerializer,
)


class SupportTicketCreateView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = SupportTicketCreateSerializer

    @extend_schema(tags=["Support"], request=SupportTicketCreateSerializer, responses={201: SupportTicketSerializer})
    def post(self, request):
        serializer = SupportTicketCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        ticket = serializer.save()
        return success_response(data=SupportTicketSerializer(ticket).data, message="Support ticket created successfully", status_code=status.HTTP_201_CREATED)


class MySupportTicketViewSet(StandardReadOnlyModelViewSet):
    serializer_class = SupportTicketSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "priority", "category"]
    ordering_fields = ["created_at", "status", "priority"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SupportTicket.objects.none()
        return SupportTicket.objects.filter(user=self.request.user, is_deleted=False).prefetch_related("messages")


class SupportTicketMessageView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = SupportTicketAddMessageSerializer
    throttle_scope = "support_message"

    @extend_schema(tags=["Support"], request=SupportTicketAddMessageSerializer, responses={201: SupportTicketSerializer})
    def post(self, request, pk: int):
        ticket = SupportTicket.objects.get(pk=pk, user=request.user, is_deleted=False)
        serializer = SupportTicketAddMessageSerializer(data=request.data, context={"request": request, "ticket": ticket})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(data=SupportTicketSerializer(ticket).data, message="Support ticket message added", status_code=status.HTTP_201_CREATED)


class DashboardSupportTicketViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = SupportTicketSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "priority", "category", "assigned_to", "user"]
    search_fields = ["subject", "user__full_name", "user__email", "user__phone_number"]
    ordering_fields = ["created_at", "status", "priority"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return SupportTicket.objects.filter(is_deleted=False).select_related("user", "assigned_to").prefetch_related("messages")


class DashboardSupportStatusView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = SupportTicketStatusSerializer

    @extend_schema(tags=["Dashboard"], request=SupportTicketStatusSerializer, responses={200: SupportTicketSerializer})
    def patch(self, request, pk: int):
        ticket = SupportTicket.objects.get(pk=pk, is_deleted=False)
        serializer = SupportTicketStatusSerializer(data=request.data, context={"request": request, "ticket": ticket})
        serializer.is_valid(raise_exception=True)
        ticket = serializer.save()
        return success_response(data=SupportTicketSerializer(ticket).data, message="Support ticket status updated")


class DashboardSupportPriorityView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = SupportTicketPrioritySerializer

    @extend_schema(tags=["Dashboard"], request=SupportTicketPrioritySerializer, responses={200: SupportTicketSerializer})
    def patch(self, request, pk: int):
        ticket = SupportTicket.objects.get(pk=pk, is_deleted=False)
        serializer = SupportTicketPrioritySerializer(data=request.data, context={"request": request, "ticket": ticket})
        serializer.is_valid(raise_exception=True)
        ticket = serializer.save()
        return success_response(data=SupportTicketSerializer(ticket).data, message="Support ticket priority updated")


class DashboardSupportAssignView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = SupportTicketAssignSerializer

    @extend_schema(tags=["Dashboard"], request=SupportTicketAssignSerializer, responses={200: SupportTicketSerializer})
    def post(self, request, pk: int):
        ticket = SupportTicket.objects.get(pk=pk, is_deleted=False)
        serializer = SupportTicketAssignSerializer(data=request.data, context={"request": request, "ticket": ticket})
        serializer.is_valid(raise_exception=True)
        ticket = serializer.save()
        return success_response(data=SupportTicketSerializer(ticket).data, message="Support ticket assigned")


class DashboardSupportMessageView(APIView):
    permission_classes = [IsAdminOrITSupport]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = SupportTicketAddMessageSerializer
    throttle_scope = "support_message"

    @extend_schema(tags=["Dashboard"], request=SupportTicketAddMessageSerializer, responses={201: SupportTicketSerializer})
    def post(self, request, pk: int):
        ticket = SupportTicket.objects.get(pk=pk, is_deleted=False)
        serializer = SupportTicketAddMessageSerializer(data=request.data, context={"request": request, "ticket": ticket})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(data=SupportTicketSerializer(ticket).data, message="Support ticket message added", status_code=status.HTTP_201_CREATED)
