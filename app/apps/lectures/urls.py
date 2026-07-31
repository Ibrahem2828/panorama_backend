from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardLectureOriginalView,
    DashboardLectureViewSet,
    LectureNoteDetailView,
    LectureNotesView,
    LectureProcessingStatusView,
    LectureViewerManifestView,
    LectureViewerPageView,
    LectureViewerSessionView,
    LectureViewerTextView,
    LectureViewerThumbnailView,
    LectureViewSet,
)

router = DefaultRouter()
router.register("lectures", LectureViewSet, basename="lectures")
router.register("dashboard/lectures", DashboardLectureViewSet, basename="dashboard-lectures")

urlpatterns = [
    *router.urls,
    path("lectures/<int:pk>/viewer/manifest/", LectureViewerManifestView.as_view(), name="lecture-viewer-manifest"),
    path("lectures/<int:pk>/viewer/session/", LectureViewerSessionView.as_view(), name="lecture-viewer-session"),
    path(
        "lectures/<int:pk>/viewer/pages/<int:page_number>/",
        LectureViewerPageView.as_view(),
        name="lecture-viewer-page",
    ),
    path(
        "lectures/<int:pk>/viewer/pages/<int:page_number>/thumbnail/",
        LectureViewerThumbnailView.as_view(),
        name="lecture-viewer-thumbnail",
    ),
    path(
        "lectures/<int:pk>/viewer/pages/<int:page_number>/text/",
        LectureViewerTextView.as_view(),
        name="lecture-viewer-text",
    ),
    path(
        "lectures/<int:pk>/processing-status/", LectureProcessingStatusView.as_view(), name="lecture-processing-status"
    ),
    path("lectures/<int:pk>/notes/", LectureNotesView.as_view(), name="lecture-notes"),
    path(
        "lectures/<int:lecture_id>/notes/<int:note_id>/",
        LectureNoteDetailView.as_view(),
        name="lecture-note-detail",
    ),
    path(
        "dashboard/lectures/<int:pk>/original/",
        DashboardLectureOriginalView.as_view(),
        name="dashboard-lecture-original",
    ),
]
