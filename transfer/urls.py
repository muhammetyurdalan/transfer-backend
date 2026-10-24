from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from health_check.views import HealthCheckView
from strawberry.django.views import AsyncGraphQLView
from core import psp
from core.schema import schema


HEALTH_CHECKS = [
    'health_check.checks.Database',
    'health_check.checks.Storage',
]

urlpatterns = [
    path('admin/', admin.site.urls),
    path(
        'graphql/',
        csrf_exempt(
            AsyncGraphQLView.as_view(
                schema=schema,
                multipart_uploads_enabled=True
            )
        )
    ),
    path('health-check/', HealthCheckView.as_view(checks=HEALTH_CHECKS)),
    path('psp-return/', csrf_exempt(psp.process_psp_return)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
