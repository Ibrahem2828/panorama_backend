from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardFilePreviewTokenView,
    DashboardFileResourceViewSet,
    FileDownloadTokenView,
    FileResourceProtectedView,
    FileResourceViewSet,
    GroupFileResourceViewSet,
)

router = DefaultRouter()
router.register("files", FileResourceViewSet, basename="files")
router.register("dashboard/files", DashboardFileResourceViewSet, basename="dashboard-files")

urlpatterns = [
    *router.urls,
    path("files/<int:pk>/download-token/", FileDownloadTokenView.as_view(), name="file-download-token"),
    path("dashboard/files/<int:pk>/preview-token/", DashboardFilePreviewTokenView.as_view(), name="dashboard-file-preview-token"),
    path("files/<int:pk>/view/", FileResourceProtectedView.as_view(), name="file-protected-view"),
    path("groups/<int:group_pk>/files/", GroupFileResourceViewSet.as_view({"get": "list"}), name="group-files"),
]
