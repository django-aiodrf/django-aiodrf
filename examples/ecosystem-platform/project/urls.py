"""Health endpoints and optional diagnostics stay separate from API routes."""

from demo.views import Identity, Ping, Records, diagnostic_page
from django.conf import settings
from django.urls import include, path
from health_check.views import HealthCheckView
from rest_framework.routers import SimpleRouter

router = SimpleRouter()
router.register("records", Records)
urlpatterns = [
    *router.urls,
    path("ping/", Ping.as_view()),
    path("identity/", Identity.as_view()),
    path(
        "health/",
        HealthCheckView.as_view(
            checks=["health_check.checks.Cache", "health_check.checks.Database"]
        ),
    ),
    path("diagnostics/", diagnostic_page),
]
if "debug_toolbar" in settings.INSTALLED_APPS:
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
if "silk" in settings.INSTALLED_APPS:
    urlpatterns += [path("silk/", include("silk.urls", namespace="silk"))]
if "django_prometheus" in settings.INSTALLED_APPS:
    urlpatterns += [path("", include("django_prometheus.urls"))]
if "wireup.integration.django" in settings.INSTALLED_APPS:
    from demo.injected import Greeting

    urlpatterns += [path("injected/", Greeting.as_view())]
