from django.urls import path
from rest_framework.routers import DefaultRouter

from .offline_views import (
    DashboardFileOfflineLicenseListView,
    DashboardFileOfflineLicenseRevokeView,
    FileOfflineLicenseListView,
    FileOfflineLicenseRenewView,
    FileOfflineLicenseView,
    FileOfflinePackageView,
    FileOfflineSyncView,
)
from .views import (
    DashboardFileResourceViewSet,
    FileAccessTicketView,
    FileResourceViewSet,
    FileViewerManifestView,
    FileViewerPageView,
    FileViewerSessionView,
    FileViewerTextView,
    FileViewerThumbnailView,
    GroupFileResourceViewSet,
    ProtectedFileStreamView,
)
from .visibility_templates import FileVisibilityTemplateViewSet

router = DefaultRouter()
router.register("files", FileResourceViewSet, basename="files")
router.register(
    "dashboard/file-visibility-templates", FileVisibilityTemplateViewSet, basename="dashboard-file-visibility-templates"
)
router.register("dashboard/files", DashboardFileResourceViewSet, basename="dashboard-files")

# Fixed-segment routes must precede the routers: ``files/<pk>/`` would otherwise swallow ``offline-licenses``.
urlpatterns = [
    path("files/offline-licenses/", FileOfflineLicenseListView.as_view(), name="file-offline-licenses"),
    path("files/offline-licenses/sync/", FileOfflineSyncView.as_view(), name="file-offline-sync"),
    path(
        "dashboard/files/offline-licenses/",
        DashboardFileOfflineLicenseListView.as_view(),
        name="dashboard-file-offline-licenses",
    ),
    path(
        "dashboard/files/offline-licenses/revoke/",
        DashboardFileOfflineLicenseRevokeView.as_view(),
        name="dashboard-file-offline-revoke",
    ),
    *router.urls,
    path("files/<int:pk>/offline-license/", FileOfflineLicenseView.as_view(), name="file-offline-license"),
    path(
        "files/<int:pk>/offline-license/renew/",
        FileOfflineLicenseRenewView.as_view(),
        name="file-offline-license-renew",
    ),
    path("files/<int:pk>/offline-package/", FileOfflinePackageView.as_view(), name="file-offline-package"),
    path("groups/<int:group_pk>/files/", GroupFileResourceViewSet.as_view({"get": "list"}), name="group-files"),
    path("files/<int:pk>/access-ticket/", FileAccessTicketView.as_view(), name="file-access-ticket"),
    path("files/<int:pk>/viewer/manifest/", FileViewerManifestView.as_view(), name="file-viewer-manifest"),
    path("files/<int:pk>/viewer/session/", FileViewerSessionView.as_view(), name="file-viewer-session"),
    path("files/<int:pk>/viewer/pages/<int:page_number>/", FileViewerPageView.as_view(), name="file-viewer-page"),
    path(
        "files/<int:pk>/viewer/pages/<int:page_number>/thumbnail/",
        FileViewerThumbnailView.as_view(),
        name="file-viewer-thumbnail",
    ),
    path(
        "files/<int:pk>/viewer/pages/<int:page_number>/text/",
        FileViewerTextView.as_view(),
        name="file-viewer-text",
    ),
    path("protected-files/<uuid:token>/", ProtectedFileStreamView.as_view(), name="protected-file-stream"),
]
