"""
Per-request decisions that ``APIView.dispatch`` keeps between requests.

Where each step of the request runs, and which renderer an ``Accept`` header
selects, are worked out once per configuration. These tests pin that the
answers are the ones computed afresh: they follow ``as_view()`` arguments,
attributes changed on the class or the instance, purity declared later and
the settings.
"""

import pytest
from django.test import override_settings
from rest_framework import parsers, renderers
from rest_framework import permissions as drf_permissions
from rest_framework import views as drf_views
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.request import Request as DRFRequest
from rest_framework.response import Response as DRFResponse
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory

from aiodrf import utils, views
from aiodrf.compat import DRF_VERSION
from aiodrf.request import Request
from aiodrf.response import Response
from aiodrf.test import AsyncAPIRequestFactory, count_hops
from aiodrf.utils import register_pure_method
from aiodrf.views import APIView

factory = AsyncAPIRequestFactory()


class Constructed(drf_permissions.AllowAny):
    """A permission with a constructor of its own: built in a thread."""

    def __init__(self):
        pass


class Hello(APIView):
    authentication_classes = ()
    permission_classes = [drf_permissions.AllowAny]

    async def get(self, request):
        return Response({"ok": True})


async def hops_of(view, **headers):
    with count_hops() as hops:
        response = await view(factory.get("/", headers=headers))
    assert response.status_code == 200, response.content
    return hops.calls


# -- Where the steps run ---------------------------------------------------------


async def test_the_decisions_are_not_recomputed_per_request(monkeypatch):
    view = Hello.as_view()
    await hops_of(view)
    calls = []
    original = views._permission_leaves

    def counting(classes):
        calls.append(classes)
        return original(classes)

    # Every step looks at the classes the view configures for it.
    monkeypatch.setattr(views, "_permission_leaves", counting)
    assert await hops_of(view) == []
    assert calls == []


async def test_as_view_arguments_are_a_configuration_of_their_own():
    inline = Hello.as_view()
    built = Hello.as_view(permission_classes=[Constructed])
    assert await hops_of(inline) == []
    assert await hops_of(built) == ["APIView._acheck.<locals>.check_now"]
    assert await hops_of(inline) == []


async def test_a_class_attribute_changed_after_as_view_is_followed(monkeypatch):
    class Changed(Hello):
        pass

    view = Changed.as_view()
    assert await hops_of(view) == []
    monkeypatch.setattr(Changed, "permission_classes", [Constructed])
    assert await hops_of(view) == ["APIView._acheck.<locals>.check_now"]
    monkeypatch.setattr(Changed, "permission_classes", [drf_permissions.AllowAny])
    assert await hops_of(view) == []
    # DRF reads the list on every request, so a list changed in place counts too.
    Changed.permission_classes.append(Constructed)
    assert await hops_of(view) == ["APIView._acheck.<locals>.check_now"]


async def test_an_attribute_set_on_the_instance_is_followed():
    class PerRequest(Hello):
        def setup(self, request, *args, **kwargs):
            super().setup(request, *args, **kwargs)
            if "HTTP_X_SLOW" in request.META:
                self.permission_classes = [Constructed]

    view = PerRequest.as_view()
    assert await hops_of(view) == []
    assert await hops_of(view, x_slow="1") == ["APIView._acheck.<locals>.check_now"]
    assert await hops_of(view) == []


async def test_a_later_declaration_of_purity_is_followed():
    class Declared(Constructed):
        def __init__(self):
            pass

    view = Hello.as_view(permission_classes=[Declared])
    assert await hops_of(view) == ["APIView._acheck.<locals>.check_now"]
    register_pure_method(Declared, "__init__")
    try:
        assert await hops_of(view) == []
    finally:
        utils._pure.methods.pop(Declared, None)
        utils._pure.changed()
    assert await hops_of(view) == ["APIView._acheck.<locals>.check_now"]


async def test_purity_declared_by_the_settings_is_followed():
    class Setting(drf_permissions.BasePermission):
        def __init__(self):
            pass

        def has_permission(self, request, view):
            return True

    view = Hello.as_view(permission_classes=[Setting])
    assert await hops_of(view) == ["APIView._acheck.<locals>.check_now"]
    with override_settings(AIODRF={"PURE_POLICIES": [Setting]}, FASTDRF={}):
        assert await hops_of(view) == []
    assert await hops_of(view) == ["APIView._acheck.<locals>.check_now"]


async def test_classes_read_from_the_settings_follow_them():
    class FromSettings(Hello):
        # What DRF reads per request, whatever it is.
        @property
        def permission_classes(self):
            return api_settings.DEFAULT_PERMISSION_CLASSES

    view = FromSettings.as_view()
    assert await hops_of(view) == []
    path = f"{__name__}.Constructed"
    with override_settings(REST_FRAMEWORK={"DEFAULT_PERMISSION_CLASSES": [path]}):
        assert await hops_of(view) == ["APIView._acheck.<locals>.check_now"]
    assert await hops_of(view) == []


async def test_a_renderer_with_a_constructor_moves_negotiation_to_a_thread(monkeypatch):
    class Built(renderers.JSONRenderer):
        def __init__(self):
            pass

    class Negotiated(Hello):
        pass

    view = Negotiated.as_view()
    assert await hops_of(view) == []
    monkeypatch.setattr(Negotiated, "renderer_classes", [Built])
    assert await hops_of(view) == ["APIView._negotiate"]


async def test_an_exception_with_an_authenticator_header_is_unchanged():
    from rest_framework.authentication import BasicAuthentication

    class Header(BasicAuthentication):
        def authenticate_header(self, request):
            return 'Basic realm="api"'

    class Denied(Hello):
        permission_classes = [drf_permissions.IsAuthenticated]

    for view, header, calls in (
        (
            Denied.as_view(authentication_classes=[BasicAuthentication]),
            'Basic realm="api"',
            [],
        ),
        (
            Denied.as_view(authentication_classes=[Header]),
            'Basic realm="api"',
            # A subclass is not a known header-based authenticator (no credentials check).
            [
                "BasicAuthentication.authenticate",
                Header.authenticate_header.__qualname__,
            ],
        ),
        (Denied.as_view(), None, []),
    ):
        for _ in range(2):
            with count_hops() as hops:
                response = await view(factory.get("/"))
            assert response.get("WWW-Authenticate") == header
            assert response.status_code == (401 if header else 403)
            assert hops.calls == calls


# -- Content negotiation -------------------------------------------------------------


class VendorRenderer(renderers.JSONRenderer):
    media_type = "application/vnd.example.v1+json"
    format = "vnd"


class PlainRenderer(renderers.BaseRenderer):
    media_type = "text/plain"
    format = "txt"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return str(data).encode()


class HTMLish(renderers.BaseRenderer):
    media_type = "text/html"
    format = "html"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return b"<p>html</p>"


RENDERER_SETS = {
    "default": None,
    "json": [renderers.JSONRenderer],
    "several": [renderers.JSONRenderer, VendorRenderer, PlainRenderer, HTMLish],
    "vendor first": [VendorRenderer, renderers.JSONRenderer],
}
ACCEPT = [
    None,
    "",
    "*/*",
    "application/json",
    "application/json; indent=4",
    "application/json;q=0.5, text/plain",
    "text/plain;q=0.1, application/json;q=0.9",
    "text/*",
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "application/vnd.example.v1+json",
    "application/vnd.example.v1+json; version=2",
    "application/*; charset=utf-8",
    "image/png",
    "image/png, */*;q=0.1",
    "*/*; indent=2",
    "text/plain, application/json",
    "application/xml",
]
FORMATS = [None, "json", "txt", "vnd", "xml"]


def negotiated(response):
    if response.status_code != 200:
        return response.status_code
    return response.data


def drf_view(renderer_classes):
    class DRFNegotiated(drf_views.APIView):
        authentication_classes = ()
        permission_classes = ()

        def get(self, request, format=None):
            return DRFResponse(
                [type(request.accepted_renderer).__name__, request.accepted_media_type]
            )

    return DRFNegotiated.as_view(
        **({} if renderer_classes is None else {"renderer_classes": renderer_classes})
    )


def aiodrf_view(renderer_classes, **initkwargs):
    class Negotiated(APIView):
        authentication_classes = ()
        permission_classes = ()

        async def get(self, request, format=None):
            return Response(
                [type(request.accepted_renderer).__name__, request.accepted_media_type]
            )

    if renderer_classes is not None:
        initkwargs["renderer_classes"] = renderer_classes
    return Negotiated.as_view(**initkwargs)


@pytest.mark.parametrize("renderer_set", RENDERER_SETS)
async def test_negotiation_matches_drf(renderer_set):
    renderer_classes = RENDERER_SETS[renderer_set]
    drf, aiodrf = drf_view(renderer_classes), aiodrf_view(renderer_classes)
    for accept in ACCEPT:
        headers = {} if accept is None else {"accept": accept}
        extra = {} if accept is None else {"HTTP_ACCEPT": accept}
        for format in FORMATS:
            for suffix in (False, True):
                query = {"format": format} if format and not suffix else None
                kwargs = {"format": format} if format and suffix else {}
                expected = negotiated(
                    drf(APIRequestFactory().get("/", query, **extra), **kwargs)
                )
                # Twice: the second answer comes from what the first one kept.
                for _ in range(2):
                    response = await aiodrf(
                        factory.get("/", query, headers=headers), **kwargs
                    )
                    assert negotiated(response) == expected, (accept, format, suffix)


async def test_each_request_gets_renderers_of_its_own():
    seen = []

    class Kept(APIView):
        async def get(self, request):
            seen.append(request.accepted_renderer)
            return Response({})

    view = Kept.as_view()
    await view(factory.get("/"))
    await view(factory.get("/"))
    assert seen[0] is not seen[1]
    assert type(seen[0]) is type(seen[1])


async def test_the_view_renderers_decide(monkeypatch):
    json_first = aiodrf_view([renderers.JSONRenderer, PlainRenderer])
    plain_first = aiodrf_view([PlainRenderer, renderers.JSONRenderer])
    for _ in range(2):
        assert (await json_first(factory.get("/"))).data[0] == "JSONRenderer"
        assert (await plain_first(factory.get("/"))).data[0] == "PlainRenderer"
    # A media type changed on the class is what the next request negotiates.
    monkeypatch.setattr(PlainRenderer, "media_type", "text/x-plain")
    response = await plain_first(factory.get("/", headers={"accept": "text/x-plain"}))
    assert response.data == ["PlainRenderer", "text/x-plain"]


async def test_failures_and_long_headers_are_not_kept(monkeypatch):
    monkeypatch.setattr(views, "_negotiations", {})
    view = aiodrf_view([renderers.JSONRenderer])
    assert (
        await view(factory.get("/", headers={"accept": "image/png"}))
    ).status_code == 406
    assert (await view(factory.get("/", {"format": "xml"}))).status_code == 404
    long = "application/json, " + "x/y, " * views._MAX_KEPT_ACCEPT
    assert (await view(factory.get("/", headers={"accept": long}))).status_code == 200
    assert views._negotiations == {}


async def test_the_negotiations_kept_are_bounded(monkeypatch):
    monkeypatch.setattr(views, "_negotiations", {})
    monkeypatch.setattr(views, "_NEGOTIATION_CACHE_SIZE", 4)
    view = aiodrf_view([renderers.JSONRenderer])
    for index in range(10):
        accept = f"application/json; v={index}"
        response = await view(factory.get("/", headers={"accept": accept}))
        assert response.data == ["JSONRenderer", accept]
        assert 0 < len(views._negotiations) <= 4


async def test_a_negotiation_of_its_own_is_not_kept(monkeypatch):
    class Last(DefaultContentNegotiation):
        def select_renderer(self, request, renderers, format_suffix=None):
            return renderers[-1], renderers[-1].media_type

    monkeypatch.setattr(views, "_negotiations", {})
    view = aiodrf_view([renderers.JSONRenderer, PlainRenderer])
    assert (await view(factory.get("/"))).data == ["JSONRenderer", "application/json"]
    kept = dict(views._negotiations)
    assert kept

    class Custom(APIView):
        content_negotiation_class = Last

        async def get(self, request):
            return Response([type(request.accepted_renderer).__name__])

    assert (await Custom.as_view()(factory.get("/"))).data == ["BrowsableAPIRenderer"]
    # DRF's class patched (a test double, an instrumentation) is DRF's no more.
    monkeypatch.setattr(
        DefaultContentNegotiation, "select_renderer", Last.select_renderer
    )
    assert (await view(factory.get("/"))).data == ["PlainRenderer", "text/plain"]
    assert views._negotiations == kept


@pytest.mark.parametrize("read_first", [False, True])
async def test_the_accept_header_is_read_as_drf_reads_it(read_first):
    # Django keeps ``request.headers`` once built; DRF reads the header there.
    class Rewritten:
        def setup(self, request, *args, **kwargs):
            if read_first:
                request.headers  # noqa: B018
            request.META["HTTP_ACCEPT"] = "text/plain"
            super().setup(request, *args, **kwargs)

    renderer_classes = [renderers.JSONRenderer, PlainRenderer]
    drf = type("DRF", (Rewritten, drf_views.APIView), {})
    ours = type("Ours", (Rewritten, APIView), {})

    def handler(self, request):
        return Response([type(request.accepted_renderer).__name__])

    drf.get = ours.get = handler
    extra = {"HTTP_ACCEPT": "application/json"}
    expected = drf.as_view(renderer_classes=renderer_classes)(
        APIRequestFactory().get("/", **extra)
    )
    view = ours.as_view(renderer_classes=renderer_classes)
    for _ in range(2):
        response = await view(factory.get("/", headers={"accept": "application/json"}))
        # DRF 3.18 reads request.headers, earlier versions request.META.
        read = (
            "JSONRenderer" if read_first and DRF_VERSION >= (3, 18) else "PlainRenderer"
        )
        assert response.data == expected.data == [read]


@pytest.mark.parametrize("attribute", ["query_params", "headers"])
async def test_what_the_request_class_reads_is_what_is_negotiated(attribute):
    from aiodrf.request import Request

    # DRF asks the request for these; a request class may answer differently.
    answers = {"query_params": {"format": "txt"}, "headers": {"accept": "text/plain"}}
    Fixed = type(
        "Fixed", (Request,), {attribute: property(lambda self: answers[attribute])}
    )

    fixed = aiodrf_view([renderers.JSONRenderer, PlainRenderer], request_class=Fixed)
    usual = aiodrf_view([renderers.JSONRenderer, PlainRenderer])
    read = attribute == "query_params" or DRF_VERSION >= (
        3,
        18,
    )  # earlier: request.META
    answer = (
        ["PlainRenderer", "text/plain"]
        if read
        else ["JSONRenderer", "application/json"]
    )
    for _ in range(2):
        assert (await fixed(factory.get("/"))).data == answer
        assert (await usual(factory.get("/"))).data == [
            "JSONRenderer",
            "application/json",
        ]


class OwnNegotiation(DefaultContentNegotiation):
    """Not DRF's negotiation, so never kept: it reads the request itself."""


@pytest.mark.parametrize("negotiation", [DefaultContentNegotiation, OwnNegotiation])
@pytest.mark.parametrize("read_first", [False, True])
@pytest.mark.parametrize("warm", [False, True])
async def test_headers_set_on_the_request_are_what_is_negotiated(
    monkeypatch, negotiation, read_first, warm
):
    # A project's ``initialize_request()`` may give DRF's request headers of its own.
    class Overridden(APIView):
        authentication_classes = ()
        permission_classes = ()
        renderer_classes = [renderers.JSONRenderer, PlainRenderer]
        content_negotiation_class = negotiation

        def initialize_request(self, request, *args, **kwargs):
            if read_first:
                request.headers  # noqa: B018 -- Django keeps them once built
            request = super().initialize_request(request, *args, **kwargs)
            if "HTTP_X_PLAIN" in request.META:
                request.headers = {"accept": "text/plain"}
            return request

        async def get(self, request):
            return Response(
                [type(request.accepted_renderer).__name__, request.accepted_media_type]
            )

    monkeypatch.setattr(views, "_negotiations", {})
    control = DRFRequest(APIRequestFactory().get("/", HTTP_ACCEPT="application/json"))
    control.headers = {"accept": "text/plain"}
    renderer, media_type = DefaultContentNegotiation().select_renderer(
        control, [renderers.JSONRenderer(), PlainRenderer()]
    )
    # DRF 3.18 reads request.headers, earlier versions request.META.
    assert media_type == (
        "text/plain" if DRF_VERSION >= (3, 18) else "application/json"
    )
    expected = [type(renderer).__name__, media_type]
    view = Overridden.as_view()
    usual = {"accept": "application/json"}
    if warm:
        assert (await view(factory.get("/", headers=usual))).data[0] == "JSONRenderer"
    # The same Accept header, requests of their own: each is negotiated as DRF would.
    for _ in range(2):
        assert (
            await view(factory.get("/", headers={**usual, "x-plain": "1"}))
        ).data == expected
        assert (await view(factory.get("/", headers=usual))).data == [
            "JSONRenderer",
            "application/json",
        ]


def plain_headers(request):
    if "HTTP_X_PLAIN" in request.META:
        request.headers = {"accept": "text/plain"}


class PlainHeadersRequest(Request):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        plain_headers(self)


class PlainHeadersInitial(APIView):
    async def ainitial(self, request, *args, **kwargs):
        plain_headers(request)
        await super().ainitial(request, *args, **kwargs)


class PlainHeadersSuffix(APIView):
    def get_format_suffix(self, **kwargs):
        plain_headers(self.request)
        return super().get_format_suffix(**kwargs)


@pytest.mark.parametrize(
    ("base", "initkwargs"),
    [
        (PlainHeadersInitial, {}),
        (PlainHeadersSuffix, {}),
        (APIView, {"request_class": PlainHeadersRequest}),
    ],
)
async def test_headers_set_before_negotiation_are_what_is_negotiated(
    monkeypatch, base, initkwargs
):
    class Negotiated(base):
        authentication_classes = ()
        permission_classes = ()

        async def get(self, request):
            return Response([type(request.accepted_renderer).__name__])

    monkeypatch.setattr(views, "_negotiations", {})
    view = Negotiated.as_view(
        renderer_classes=[renderers.JSONRenderer, PlainRenderer], **initkwargs
    )
    usual = {"accept": "application/json"}
    # DRF 3.18 reads request.headers, earlier versions request.META.
    expected = ["PlainRenderer" if DRF_VERSION >= (3, 18) else "JSONRenderer"]
    for _ in range(2):
        assert (await view(factory.get("/", headers=usual))).data == ["JSONRenderer"]
        assert (
            await view(factory.get("/", headers={**usual, "x-plain": "1"}))
        ).data == expected


async def test_the_negotiations_a_plan_keeps_are_bounded(monkeypatch):
    monkeypatch.setattr(views, "_NEGOTIATION_CACHE_SIZE", 4)

    class Planned(APIView):
        authentication_classes = ()
        permission_classes = ()
        renderer_classes = [renderers.JSONRenderer]

        async def get(self, request):
            return Response(request.accepted_media_type)

    view = Planned.as_view()
    plan = views._class_plan(Planned)
    assert plan is not None
    for index in range(10):
        accept = f"application/json; v={index}"
        response = await view(factory.get("/", headers={"accept": accept}))
        assert response.data == accept
        assert 0 < len(plan.negotiations) <= 4


@pytest.mark.parametrize("declared", [tuple, list])
def test_a_view_declaring_tuples_or_lists_has_a_plan(declared):
    class Declared(APIView):
        authentication_classes = declared()
        permission_classes = declared()
        parser_classes = declared([parsers.JSONParser])
        renderer_classes = declared([renderers.JSONRenderer])

    assert views._request_plan(Declared()) is not None


# -- Viewsets ----------------------------------------------------------------------


def _viewset(**attributes):
    from rest_framework.decorators import action

    from aiodrf import viewsets

    class Seen(viewsets.ViewSet):
        authentication_classes = ()
        permission_classes = ()

        async def list(self, request):
            return Response({"action": self.action})

        async def create(self, request):
            return Response({"action": self.action})

        @action(detail=False, permission_classes=[drf_permissions.IsAuthenticated])
        async def private(self, request):
            return Response({"action": self.action})

    return type("Seen", (Seen,), attributes)


def test_a_viewset_has_a_plan():
    assert views._class_plan(_viewset()) is not None


async def test_a_planned_viewset_sets_the_action_as_drf_does():
    viewset = _viewset()
    view = viewset.as_view({"get": "list", "post": "create"})
    with count_hops() as hops:
        assert (await view(factory.get("/"))).data == {"action": "list"}
    assert hops.count == 0
    assert (await view(factory.post("/"))).data == {"action": "create"}
    # No handler: DRF's 405, with ``action`` None.
    response = await view(factory.put("/"))
    assert response.status_code == 405
    response = await view(factory.options("/"))
    assert response.status_code == 200


async def test_the_action_is_set_before_the_policies_run():
    seen = []

    class Recording(drf_permissions.BasePermission):
        def has_permission(self, request, view):
            seen.append(view.action)
            return True

    viewset = _viewset(permission_classes=[Recording])
    view = viewset.as_view({"get": "list"})
    await view(factory.get("/"))
    await view(factory.options("/"))
    await view(factory.delete("/"))
    assert seen == ["list", "metadata", None]


def test_a_viewset_with_its_own_initialize_request_has_no_plan():
    def initialize_request(self, request, *args, **kwargs):
        return super(type(self), self).initialize_request(request, *args, **kwargs)

    assert views._class_plan(_viewset(initialize_request=initialize_request)) is None


def test_drfs_viewset_mixin_with_aiodrfs_view_has_no_plan():
    from rest_framework import viewsets as drf_viewsets

    class Mixed(drf_viewsets.ViewSetMixin, APIView):
        pass

    assert views._class_plan(Mixed) is None


async def test_an_actions_own_permissions_are_the_plans_classes():
    view = _viewset().as_view(
        {"get": "private"}, permission_classes=[drf_permissions.IsAuthenticated]
    )
    response = await view(factory.get("/"))
    assert response.status_code == 403


async def test_a_viewset_without_an_action_map_fails_as_in_drf():
    # Built without ``as_view(actions)``: DRF's ``initialize_request`` reads
    # ``action_map`` and raises.
    viewset = _viewset()
    view = viewset()
    view.setup(factory.get("/"))
    view.args, view.kwargs = (), {}
    view.headers = {}
    with pytest.raises(AttributeError, match="action_map"):
        await view.dispatch(factory.get("/"))
