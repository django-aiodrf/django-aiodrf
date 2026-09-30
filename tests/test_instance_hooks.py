"""
Hooks set on a policy instance: the class-level short paths (no credentials,
session authentication, CSRF, lazy authentication, rate throttles) must not
apply when the instance replaces the method they reason about.
"""

from django.contrib.auth.models import AnonymousUser, User
from django.core.cache.backends.locmem import LocMemCache
from django.utils.asyncio import async_unsafe
from rest_framework import authentication as drf_authentication
from rest_framework import permissions as drf_permissions
from rest_framework import throttling as drf_throttling
from rest_framework.authentication import TokenAuthentication

from aiodrf import policies
from aiodrf.authentication import asession_authenticate
from aiodrf.request import Request
from aiodrf.test import AsyncAPIRequestFactory

USER = User(username="cookie", is_active=True)


def _request(method="get"):
    return Request(getattr(AsyncAPIRequestFactory(), method)("/"))


async def test_an_instance_authenticate_runs_without_the_classs_header():
    # TokenAuthentication's registered check sees no ``Token`` header.
    authenticator = TokenAuthentication()
    authenticator.authenticate = lambda request: (USER, None)
    assert await policies.aauthenticate(authenticator, _request()) == (USER, None)


async def test_an_instance_authenticate_replaces_the_session_flow():
    authenticator = drf_authentication.SessionAuthentication()
    authenticator.authenticate = lambda request: (USER, None)
    assert await policies.aauthenticate(authenticator, _request()) == (USER, None)


async def test_an_instance_csrf_check_is_not_called_on_the_loop():
    calls = []

    @async_unsafe("enforce_csrf on the loop")
    def enforce_csrf(request):
        calls.append(request)

    authenticator = drf_authentication.SessionAuthentication()
    authenticator.enforce_csrf = enforce_csrf
    request = _request()
    request._request.user = USER
    assert await asession_authenticate(authenticator, request) == (USER, None)
    assert calls == [request]


def test_an_instance_permission_hook_may_read_the_user():
    allow = drf_permissions.AllowAny()

    async def ahas_permission(request, view):
        return request.user.is_authenticated

    allow.ahas_permission = ahas_permission
    pairs = (("has_permission", "ahas_permission"),)
    assert policies.reads_user([drf_permissions.AllowAny()], *pairs) is False
    assert policies.reads_user([allow], *pairs) is True


async def test_an_instance_throttle_hook_runs_off_the_loop():
    throttle = drf_throttling.AnonRateThrottle()
    throttle.cache = LocMemCache("instance-hooks", {})
    assert policies.throttles_mode([throttle]) is policies.Mode.INLINE

    @async_unsafe("get_cache_key on the loop")
    def get_cache_key(request, view):
        return "instance"

    throttle.get_cache_key = get_cache_key
    assert policies.throttles_mode([throttle]) is policies.Mode.THREAD
    request = _request()
    request._request.user = AnonymousUser()
    assert await policies.aallow_request(throttle, request, None) is True
