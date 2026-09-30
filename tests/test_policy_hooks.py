"""
What a project writes for DRF around its policies runs where DRF would run
it, never on the event loop: a throttle's ``get_ident()``, a view's
``permission_denied()`` / ``throttled()``, authenticators reached through an
async permission, a middleware's lazy ``request.user``.
``async_unsafe`` stands for a project's database, cache or HTTP work.
"""

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.core.cache import cache
from django.core.cache.backends.locmem import LocMemCache
from django.utils.asyncio import async_unsafe
from django.utils.functional import SimpleLazyObject
from rest_framework import authentication as drf_authentication
from rest_framework import permissions as drf_permissions
from rest_framework import throttling as drf_throttling
from rest_framework.response import Response

from aiodrf import views
from aiodrf.test import AsyncAPIRequestFactory
from aiodrf.throttling import FixedWindowRateThrottle, ScopedFixedWindowRateThrottle
from aiodrf.utils import register_pure_method

factory = AsyncAPIRequestFactory()


def view(**attrs):
    class View(views.APIView):
        authentication_classes = []
        permission_classes = []

        async def get(self, request):
            user = getattr(request.user, "username", None)
            return Response({"user": user or None})

    for name, value in attrs.items():
        setattr(View, name, value)
    return View.as_view()


# -- Throttles ---------------------------------------------------------------------


class IdentThrottle(drf_throttling.AnonRateThrottle):
    rate = "100/min"

    @async_unsafe("get_ident ran on the event loop")
    def get_ident(self, request):
        return "client-1"


async def test_a_throttles_own_get_ident_runs_in_a_worker():
    response = await view(throttle_classes=[IdentThrottle])(factory.get("/"))
    assert response.status_code == 200


class HalfLocalCache(LocMemCache):
    """get/set are local (declared pure); add/incr reach another tier."""

    def get(self, *args, **kwargs):
        return super().get(*args, **kwargs)

    def set(self, *args, **kwargs):
        return super().set(*args, **kwargs)

    @async_unsafe("add ran on the event loop")
    def add(self, *args, **kwargs):
        return super().add(*args, **kwargs)

    @async_unsafe("incr ran on the event loop")
    def incr(self, *args, **kwargs):
        return super().incr(*args, **kwargs)


register_pure_method(HalfLocalCache, "get", "set")


class ScopedFixed(ScopedFixedWindowRateThrottle):
    cache = HalfLocalCache("half", {})
    THROTTLE_RATES = {"x": "10/min"}


async def test_a_scoped_fixed_window_throttle_checks_add_and_incr():
    response = await view(throttle_classes=[ScopedFixed], throttle_scope="x")(
        factory.get("/")
    )
    assert response.status_code == 200
    assert issubclass(ScopedFixed, FixedWindowRateThrottle)


# -- View hooks --------------------------------------------------------------------


class Audited(views.APIView):
    authentication_classes = []
    calls: list[str] = []

    @async_unsafe("permission_denied ran on the event loop")
    def permission_denied(self, request, message=None, code=None):
        self.calls.append("permission_denied")
        super().permission_denied(request, message, code)

    @async_unsafe("throttled ran on the event loop")
    def throttled(self, request, wait):
        self.calls.append("throttled")
        super().throttled(request, wait)

    async def get(self, request):
        return Response({})


async def test_a_views_own_permission_denied_and_throttled_run_in_a_worker():
    Audited.calls = []
    denied = type(
        "Denied", (Audited,), {"permission_classes": [drf_permissions.IsAuthenticated]}
    )
    assert (await denied.as_view()(factory.get("/"))).status_code == 403
    cache.clear()
    limited = type(
        "Limited",
        (Audited,),
        {
            "permission_classes": [],
            "throttle_classes": [drf_throttling.AnonRateThrottle],
        },
    ).as_view()
    for _ in range(3):  # tests.settings: anon 3/min
        assert (await limited(factory.get("/"))).status_code == 200
    throttled = await limited(factory.get("/"))
    assert throttled.status_code == 429
    assert Audited.calls == ["permission_denied", "throttled"]


# -- Authentication ------------------------------------------------------------------


class QueryingAuthentication(drf_authentication.BaseAuthentication):
    @async_unsafe("authenticate ran on the event loop")
    def authenticate(self, request):
        return (User(username="token-user"), None)


class AsyncOnDRFBase(drf_permissions.BasePermission):
    async def ahas_permission(self, request, view):
        return request.user.is_authenticated


class LazyAuthentication(views.APIView):
    authentication_classes = [QueryingAuthentication]
    permission_classes = [AsyncOnDRFBase]

    def perform_authentication(self, request):
        pass  # DRF's idiom: authenticate when request.user is first read

    async def get(self, request):
        return Response({"user": request.user.username})


async def test_an_async_permission_on_drfs_base_authenticates_in_a_worker():
    response = await LazyAuthentication.as_view()(factory.get("/"))
    assert response.status_code == 200
    assert response.data == {"user": "token-user"}


def session_request(middleware_user, session_user):
    request = factory.get("/")
    # Another middleware's lazy user, after Django's AuthenticationMiddleware.
    request.user = SimpleLazyObject(
        async_unsafe("lazy user on the loop")(lambda: middleware_user)
    )

    async def auser():
        return session_user

    request.auser = auser
    return request


@pytest.mark.parametrize(
    ("middleware_user", "expected"),
    [(AnonymousUser(), None), (User(username="jwt-user", is_active=True), "jwt-user")],
)
async def test_a_later_middlewares_lazy_user_is_the_session_user(
    middleware_user, expected
):
    session_user = User(username="session-user", is_active=True)
    session = view(authentication_classes=[drf_authentication.SessionAuthentication])
    response = await session(session_request(middleware_user, session_user))
    assert response.data == {"user": expected}


# -- permission_denied() and throttled() that return -------------------------------


class Soft(drf_permissions.BasePermission):
    code = message = "soft"

    async def ahas_permission(self, request, view):
        return False

    async def ahas_object_permission(self, request, view, obj):
        return False


class Hard(Soft):
    code = message = "hard"


class SoftDenials(views.APIView):
    # A log-only rule: DRF goes on to the next permission when this returns.
    authentication_classes = []
    permission_classes = [Soft, Hard]
    calls: list[str] = []

    def permission_denied(self, request, message=None, code=None):
        self.calls.append(code)
        if code != "soft":
            super().permission_denied(request, message, code)

    async def get(self, request):
        return Response({})


async def test_every_denying_permission_reaches_permission_denied_as_in_drf():
    from rest_framework.exceptions import PermissionDenied

    SoftDenials.calls = []
    response = await SoftDenials.as_view()(factory.get("/"))
    assert response.status_code == 403
    assert SoftDenials.calls == ["soft", "hard"]
    SoftDenials.calls = []
    view = SoftDenials()
    view.setup(factory.get("/"))
    request = view.initialize_request(view.request)
    with pytest.raises(PermissionDenied):
        await view.acheck_object_permissions(request, object())
    assert SoftDenials.calls == ["soft", "hard"]


class LogOnlyThrottled(views.APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [drf_throttling.AnonRateThrottle]
    calls: list[float] = []

    def throttled(self, request, wait):
        self.calls.append(wait)  # returns: the request goes on

    async def get(self, request):
        return Response({})


async def test_a_throttled_override_that_returns_runs_once():
    cache.clear()
    LogOnlyThrottled.calls = []
    view = LogOnlyThrottled.as_view()
    statuses = [(await view(factory.get("/"))).status_code for _ in range(4)]
    assert statuses == [200] * 4
    assert len(LogOnlyThrottled.calls) == 1


class HeaderAuthentication(drf_authentication.BaseAuthentication):
    def authenticate(self, request):
        return None

    @async_unsafe("authenticate_header ran on the event loop")
    def authenticate_header(self, request):
        return 'Bearer realm="api"'


async def test_an_authenticators_own_header_is_built_in_a_worker():
    needs_user = view(
        authentication_classes=[HeaderAuthentication],
        permission_classes=[drf_permissions.IsAuthenticated],
    )
    response = await needs_user(factory.get("/"))
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == 'Bearer realm="api"'


SERVICE_USER = User(username="service", pk=10**6)


class Authenticating(drf_authentication.BaseAuthentication):
    def authenticate(self, request):
        return None


class SetsUser(views.APIView):
    authentication_classes = [Authenticating]
    permission_classes = [drf_permissions.IsAuthenticated]

    def perform_authentication(self, request):
        request.user = SERVICE_USER

    async def get(self, request):
        return Response({"user": request.user.username})


class ASetsUser(SetsUser):
    async def aperform_authentication(self, request):
        request.user = SERVICE_USER


@pytest.mark.parametrize("view", [SetsUser, ASetsUser])
async def test_a_user_set_by_the_view_is_kept_for_permissions(view):
    # DRF's ``request.user`` authenticates only when no user is set.
    response = await view.as_view()(AsyncAPIRequestFactory().get("/"))
    assert response.status_code == 200, response.data
    assert response.data == {"user": "service"}


class ThrottledSetsUser(SetsUser):
    permission_classes = []

    class OnePerUser(drf_throttling.UserRateThrottle):
        rate = "1/min"

        def get_cache_key(self, request, view):
            assert request.user is SERVICE_USER

    throttle_classes = [OnePerUser]


async def test_a_user_set_by_the_view_is_kept_for_throttles():
    response = await ThrottledSetsUser.as_view()(AsyncAPIRequestFactory().get("/"))
    assert response.status_code == 200, response.data
