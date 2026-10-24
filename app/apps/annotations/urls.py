from django.urls import path

from .views import AnnotationListView, AnnotationSyncView, MyNotesView

urlpatterns = [
    path("annotations/<str:kind>/<int:pk>/", AnnotationListView.as_view(), name="annotation-list"),
    path("annotations/<str:kind>/<int:pk>/sync/", AnnotationSyncView.as_view(), name="annotation-sync"),
    path("my-notes/", MyNotesView.as_view(), name="my-notes"),
]
