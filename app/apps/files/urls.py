from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import DashboardFileResourceViewSet, FileResourceViewSet, GroupFileResourceViewSet

router = DefaultRouter()
router.register("files", FileResourceViewSet, basename="files")
router.register("dashboard/files", DashboardFileResourceViewSet, basename="dashboard-files")

urlpatterns = [
    *router.urls,
    path("groups/<int:group_pk>/files/", GroupFileResourceViewSet.as_view({"get": "list"}), name="group-files"),
]
