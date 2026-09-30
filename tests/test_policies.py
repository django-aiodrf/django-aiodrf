import time

from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils.asyncio import async_unsafe
from rest_framework import authentication as drf_authentication
from rest_framework import views as drf_views
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response as DRFResponse
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import AnonRateThrottle

from aiodrf import authentication, pagination, throttling
from aiodrf.response import Response
from aiodrf.test import AsyncAPIRequestFactory, count_hops
from aiodrf.views import APIView


class LazyAuthentication:
    # DRF documents this override: authentication then happens the first
    # time ``request.user`` or ``request.auth`` is read, if ever.
    authentication_classes = [TokenAuthentication]

    def perform_authentication(self, request):
        pass


def lazy_views(permission_classes, throttle_classes):
    attrs = {
        "permission_classes": permission_classes,
        "throttle_classes": throttle_classes,
    }

    class DRFView(LazyAuthentication, drf_views.APIView):
        def get(self, request):
            return DRFResponse({"ok": True})

    class AioView(LazyAuthentication, APIView):
        async def get(self, request):
            return Response({"ok": True})

    return DRFView.as_view(**attrs), AioView.as_view(**attrs)


class LazyAuthenticationParityTests(TestCase):
    SCENARIOS = [
        # Nothing reads ``request.user``: the invalid token is never looked at.
        ([AllowAny], [], 200),
        ([], [], 200),
        # Something does.
        ([IsAuthenticated], [], 401),
        ([AllowAny], [AnonRateThrottle], 401),
    ]

    async def test_invalid_credentials(self):
        for permission_classes, throttle_classes, expected in self.SCENARIOS:
            with self.subTest(
                permissions=permission_classes, throttles=throttle_classes
            ):
                cache.clear()
                drf_view, aio_view = lazy_views(permission_classes, throttle_classes)
                request = APIRequestFactory().get(
                    "/", HTTP_AUTHORIZATION="Token invalid"
                )
                drf_response = await sync_to_async(drf_view)(request)
                request = APIRequestFactory().get(
                    "/", HTTP_AUTHORIZATION="Token invalid"
                )
                aio_response = await aio_view(request)
                assert drf_response.status_code == expected
                assert aio_response.status_code == expected

    async def test_unread_credentials_cost_no_hop(self):
        user = await User.objects.acreate(username="alice")
        token = await Token.objects.acreate(user=user)
        _, view = lazy_views([AllowAny], [])
        request = AsyncAPIRequestFactory().get(
            "/", HTTP_AUTHORIZATION=f"Token {token.key}"
        )
        with count_hops() as hops:
            response = await view(request)
        assert response.status_code == 200
        # One hop for the synchronous ``perform_authentication`` override.
        assert hops.calls == ["LazyAuthentication.perform_authentication"]

    async def test_denial_authenticates_for_the_response(self):
        # ``permission_denied`` reads ``request.successful_authenticator``.
        _, view = lazy_views([~AllowAny], [])
        response = await view(
            AsyncAPIRequestFactory().get("/", HTTP_AUTHORIZATION="Token invalid")
        )
        assert response.status_code == 401


class HeaderAuthentication(authentication.BaseAuthentication):
    async def aauthenticate(self, request):
        name = request.headers.get("X-User")
        return (await User.objects.aget(username=name), None) if name else None


class OddSecondsThrottle(throttling.BaseThrottle):
    async def aallow_request(self, request, view):
        return request.headers.get("X-Allow") == "1"


class FirstItemPagination(pagination.BasePagination):
    async def apaginate_queryset(self, queryset, request, view=None):
        return [item async for item in queryset[:1]]

    def get_paginated_response(self, data):
        return Response({"results": data})


class BridgeBaseTests(TestCase):
    # Policies implemented only with the async member still work where DRF
    # calls the synchronous one (sync views, the browsable API).
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="alice")

    async def test_authentication(self):
        request = APIRequestFactory().get("/", HTTP_X_USER="alice")
        user, auth = await sync_to_async(HeaderAuthentication().authenticate)(request)
        assert (user, auth) == (self.user, None)
        assert await HeaderAuthentication().aauthenticate(request) == (self.user, None)

    async def test_throttle(self):
        allowed = APIRequestFactory().get("/", HTTP_X_ALLOW="1")
        throttle = OddSecondsThrottle()
        assert await sync_to_async(throttle.allow_request)(allowed, None) is True
        assert (
            await sync_to_async(throttle.allow_request)(
                APIRequestFactory().get("/"), None
            )
            is False
        )

    async def test_pagination(self):
        queryset = User.objects.order_by("pk")
        paginate = FirstItemPagination().paginate_queryset
        assert await sync_to_async(paginate)(queryset, None) == [self.user]
        assert issubclass(FirstItemPagination, PageNumberPagination) is False


class ApiKeyAuthentication(drf_authentication.BaseAuthentication):
    """Third-party style: synchronous, and a query when the header is there."""

    def authenticate(self, request):
        key = request.META.get("HTTP_X_API_KEY")
        if key is None:
            return None
        return User.objects.get(username=key), key


class AuditedApiKeyAuthentication(ApiKeyAuthentication):
    def authenticate(self, request):
        User.objects.count()
        return super().authenticate(request)


class CredentialsCheckTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create_user("key-1")

    def setUp(self):
        checks = authentication._CREDENTIAL_CHECKS
        self.addCleanup(checks.pop, ApiKeyAuthentication, None)
        authentication.register_credentials_check(
            ApiKeyAuthentication,
            lambda authenticator, request: "HTTP_X_API_KEY" in request.META,
        )

    def view(self, authentication_class):
        class WhoAmI(APIView):
            authentication_classes = [authentication_class]
            permission_classes = [AllowAny]

            async def get(self, request):
                return Response({"user": getattr(request.user, "username", None)})

        return WhoAmI.as_view()

    async def test_no_hop_for_a_request_without_credentials(self):
        with count_hops() as hops:
            response = await self.view(ApiKeyAuthentication)(
                AsyncAPIRequestFactory().get("/")
            )
        assert response.data == {"user": ""}
        assert hops.calls == []

    async def test_one_hop_for_a_request_with_credentials(self):
        request = AsyncAPIRequestFactory().get("/", HTTP_X_API_KEY="key-1")
        with count_hops() as hops:
            response = await self.view(ApiKeyAuthentication)(request)
        assert response.data == {"user": "key-1"}
        assert hops.count == 1, hops.calls

    async def test_a_subclass_that_overrides_authenticate_is_asked_in_a_thread(self):
        # Its ``authenticate()`` may do anything before it looks at the header.
        view = self.view(AuditedApiKeyAuthentication)
        with count_hops() as hops:
            response = await view(AsyncAPIRequestFactory().get("/"))
        assert response.data == {"user": ""}
        assert hops.count == 1, hops.calls


class LateRegistrationTests(TestCase):
    async def test_a_check_registered_after_a_request_is_used_by_the_next(self):
        class Late(ApiKeyAuthentication):
            # The class a check is registered for defines ``authenticate``.
            def authenticate(self, request):
                return super().authenticate(request)

        view = CredentialsCheckTests.view(self, Late)
        with count_hops() as hops:
            await view(AsyncAPIRequestFactory().get("/"))
        assert hops.count == 1
        checks = authentication._CREDENTIAL_CHECKS
        self.addCleanup(checks.pop, Late, None)
        authentication.register_credentials_check(
            Late, lambda authenticator, request: "HTTP_X_API_KEY" in request.META
        )
        with count_hops() as hops:
            await view(AsyncAPIRequestFactory().get("/"))
        assert hops.count == 0


class HeaderApiKeyAuthentication(ApiKeyAuthentication):
    # DRF's pattern: authenticate() reads a header through another method.
    def authenticate(self, request):
        key = self.get_key(request)
        if key is None:
            return None
        return User.objects.get(username=key), key

    def get_key(self, request):
        return request.META.get("HTTP_X_API_KEY")


class RenamedKey(HeaderApiKeyAuthentication):
    header = "HTTP_X_KEY"  # data only


class LookedUpKey(HeaderApiKeyAuthentication):
    @async_unsafe("get_key ran on the event loop")
    def get_key(self, request):
        return super().get_key(request)


class SubclassCredentialsCheckTests(TestCase):
    def setUp(self):
        checks = authentication._CREDENTIAL_CHECKS
        self.addCleanup(checks.pop, HeaderApiKeyAuthentication, None)
        authentication.register_credentials_check(
            HeaderApiKeyAuthentication,
            lambda authenticator, request: authenticator.get_key(request) is not None,
        )

    async def test_a_subclass_adding_code_is_asked_in_a_thread(self):
        for authentication_class, hops_expected in ((RenamedKey, 0), (LookedUpKey, 1)):
            with self.subTest(authentication_class.__name__):
                view = CredentialsCheckTests.view(self, authentication_class)
                with count_hops() as hops:
                    response = await view(AsyncAPIRequestFactory().get("/"))
                assert response.data == {"user": ""}
                assert hops.count == hops_expected, hops.calls


def _guarded(name, method):
    return async_unsafe(f"{name} ran on the event loop")(method)


class DeniedAnon(AnonRateThrottle):
    rate = "0/min"


class AuditedFailure(DeniedAnon):
    throttle_failure = _guarded("throttle_failure", AnonRateThrottle.throttle_failure)


class AuditedWait(DeniedAnon):
    wait = _guarded("wait", AnonRateThrottle.wait)


class CustomTimer(DeniedAnon):
    timer = staticmethod(_guarded("timer", time.time))


class ThrottleHookTests(TestCase):
    async def test_a_throttles_overridden_hooks_run_in_a_worker(self):
        for throttle in (AuditedFailure, AuditedWait, CustomTimer):
            with self.subTest(throttle.__name__):

                class Throttled(APIView):
                    authentication_classes = []
                    permission_classes = []
                    throttle_classes = [throttle]

                    async def get(self, request):
                        return Response({})

                response = await Throttled.as_view()(AsyncAPIRequestFactory().get("/"))
                assert response.status_code == 429
                assert response["Retry-After"] == "60"
