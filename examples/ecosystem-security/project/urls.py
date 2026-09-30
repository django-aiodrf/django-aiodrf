"""Vendor authentication endpoints alongside protected aiodrf resources."""

from demo.views import (
    AllauthIdentity,
    Articles,
    CookieIdentity,
    Identity,
    Notes,
    OAuthIdentity,
    TokenIdentity,
)
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import SimpleRouter

router = SimpleRouter()
router.register("articles", Articles)
router.register("notes", Notes)
urlpatterns = [
    path("admin/", admin.site.urls),
    path("oauth/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    path("djoser/", include("djoser.urls")),
    path("djoser/", include("djoser.urls.authtoken")),
    path("dj-rest-auth/", include("dj_rest_auth.urls")),
    path("_allauth/", include("allauth.headless.urls")),
    path("identity/", Identity.as_view()),
    path("identity/oauth/", OAuthIdentity.as_view()),
    path("identity/allauth/", AllauthIdentity.as_view()),
    path("identity/cookie/", CookieIdentity.as_view()),
    path("identity/token/", TokenIdentity.as_view()),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    *router.urls,
]
