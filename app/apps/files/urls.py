from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardFileResourceViewSet,
    FileAccessTicketView,
    FileResourceViewSet,
    GroupFileResourceViewSet,
    ProtectedFileStreamView,
)

router = DefaultRouter()
router.register("files", FileResourceViewSet, basename="files")
router.register("dashboard/files", DashboardFileResourceViewSet, basename="dashboard-files")

urlpatterns = [
    *router.urls,
    path("groups/<int:group_pk>/files/", GroupFileResourceViewSet.as_view({"get": "list"}), name="group-files"),
    path("files/<int:pk>/access-ticket/", FileAccessTicketView.as_view(), name="file-access-ticket"),
    path("protected-files/<uuid:token>/", ProtectedFileStreamView.as_view(), name="protected-file-stream"),
]
