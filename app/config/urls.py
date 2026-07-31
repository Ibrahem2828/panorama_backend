from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.accounts.dashboard_urls")),
    path("api/v1/", include("apps.common.urls")),
    path("api/v1/students/", include("apps.accounts.student_urls")),
    path("api/v1/", include("apps.universities.urls")),
    path("api/v1/", include("apps.verification.urls")),
    path("api/v1/", include("apps.groups.urls")),
    path("api/v1/", include("apps.chat.urls")),
    path("api/v1/", include("apps.files.urls")),
    path("api/v1/", include("apps.lectures.urls")),
    path("api/v1/", include("apps.printing.urls")),
    path("api/v1/", include("apps.announcements.urls")),
    path("api/v1/", include("apps.notifications.urls")),
    path("api/v1/", include("apps.support.urls")),
    path("api/v1/", include("apps.audit.urls")),
    path("api/v1/", include("apps.feedback.urls")),
]

if settings.DEBUG or settings.API_DOCS_ENABLED:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
