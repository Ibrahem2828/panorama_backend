from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.permissions import IsPrintStaffOrAdmin
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import PrintOrder, PrintOrderStatus
from .serializers import (
    PrintOrderAssignSerializer,
    PrintOrderCreateSerializer,
    PrintOrderNoteSerializer,
    PrintOrderSerializer,
    PrintStatusUpdateSerializer,
)
from .services import PrintStatusService


class PrintOrderCreateView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = PrintOrderCreateSerializer
    throttle_scope = "print_order"

    @extend_schema(tags=["Printing"], request=PrintOrderCreateSerializer, responses={201: PrintOrderSerializer})
    def post(self, request):
        serializer = PrintOrderCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return success_response(data=PrintOrderSerializer(order).data, message="Print order created successfully", status_code=status.HTTP_201_CREATED)


class MyPrintOrderViewSet(StandardReadOnlyModelViewSet):
    serializer_class = PrintOrderSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "priority"]
    ordering_fields = ["created_at", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PrintOrder.objects.none()
        return PrintOrder.objects.filter(user=self.request.user, is_deleted=False).prefetch_related("items", "status_history")


class PrintOrderCancelView(APIView):
    serializer_class = PrintOrderSerializer

    @extend_schema(tags=["Printing"])
    def post(self, request, pk: int):
        order = PrintOrder.objects.get(pk=pk, user=request.user, is_deleted=False)
        order = PrintStatusService.change_status(order, PrintOrderStatus.CANCELLED, request.user, note="Cancelled by user")
        return success_response(data=PrintOrderSerializer(order).data, message="Print order cancelled successfully")


class DashboardPrintOrderViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [IsPrintStaffOrAdmin]
    serializer_class = PrintOrderSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "priority", "user", "assigned_to"]
    search_fields = ["user__full_name", "user__email", "user__phone_number", "id"]
    ordering_fields = ["created_at", "status", "priority"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return PrintOrder.objects.filter(is_deleted=False).select_related("user", "assigned_to").prefetch_related("items", "status_history")


class DashboardPrintAssignView(APIView):
    permission_classes = [IsPrintStaffOrAdmin]
    serializer_class = PrintOrderAssignSerializer

    @extend_schema(tags=["Dashboard"], request=PrintOrderAssignSerializer, responses={200: PrintOrderSerializer})
    def patch(self, request, pk: int):
        order = PrintOrder.objects.get(pk=pk, is_deleted=False)
        serializer = PrintOrderAssignSerializer(data=request.data, context={"request": request, "order": order})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return success_response(data=PrintOrderSerializer(order).data, message="Print order assigned successfully")


class DashboardPrintStatusView(APIView):
    permission_classes = [IsPrintStaffOrAdmin]
    serializer_class = PrintStatusUpdateSerializer

    @extend_schema(tags=["Dashboard"], request=PrintStatusUpdateSerializer, responses={200: PrintOrderSerializer})
    def patch(self, request, pk: int):
        order = PrintOrder.objects.get(pk=pk, is_deleted=False)
        serializer = PrintStatusUpdateSerializer(data=request.data, context={"request": request, "order": order})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return success_response(data=PrintOrderSerializer(order).data, message="Print order status updated successfully")


class DashboardPrintNoteView(APIView):
    permission_classes = [IsPrintStaffOrAdmin]
    serializer_class = PrintOrderNoteSerializer

    @extend_schema(tags=["Dashboard"], request=PrintOrderNoteSerializer, responses={200: PrintOrderSerializer})
    def post(self, request, pk: int):
        order = PrintOrder.objects.get(pk=pk, is_deleted=False)
        serializer = PrintOrderNoteSerializer(data=request.data, context={"request": request, "order": order})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return success_response(data=PrintOrderSerializer(order).data, message="Print order note updated successfully")
