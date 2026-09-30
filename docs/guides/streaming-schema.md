# Documenting SSE and NDJSON items

Install `django-aiodrf[spectacular]` and use the ordinary
[drf-spectacular setup](ecosystem.md#openapi-and-swagger-ui).
`aiodrf.contrib.spectacular.StreamSchema` is a schema-only annotation. It adds no
routes, response validation, settings or runtime patches.

Use the same serializer for the annotation and for producing each item:

```python
from drf_spectacular.utils import OpenApiResponse, extend_schema

from aiodrf import serializers
from aiodrf.contrib.spectacular import StreamSchema
from aiodrf.response import EventStreamResponse, ServerSentEvent
from aiodrf.views import APIView


class UpdateSerializer(serializers.Serializer):
    sequence = serializers.IntegerField(min_value=0)
    message = serializers.CharField()


class UpdatesView(APIView):
    @extend_schema(
        responses={
            (200, "text/event-stream"): OpenApiResponse(
                StreamSchema(UpdateSerializer),
                description="JSON updates in SSE data fields.",
            ),
        }
    )
    async def get(self, request):
        async def events():
            # Replace this finite example with the application's async source.
            for sequence in range(3):
                serializer = UpdateSerializer(
                    {
                        "sequence": sequence,
                        "message": f"Update {sequence}",
                    }
                )
                yield ServerSentEvent(await serializer.adata(), id=str(sequence))

        return EventStreamResponse(events())
```

For NDJSON, annotate `(200, "application/x-ndjson")` and return
`StreamingResponse(items())`, yielding serialized items rather than
`ServerSentEvent` objects. The status and media-type tuple is spectacular's
`extend_schema(responses=...)` API; use the real status code, non-200 codes
included. Error responses keep their JSON serializer under a separate
`(400, "application/json")` key. Use `OpenApiResponse` for descriptions and
`OpenApiExample` with wire text, not an unframed item, for body examples.

## Schema representation and limits

OpenAPI 3.0 and 3.1 have no standard item contract for these text streams. The
generated response refers to a component such as `UpdateStream`:

```yaml
UpdateStream:
  type: string
  description: Item stream, not a JSON array. ...
  x-aiodrf-item-schema:
    $ref: '#/components/schemas/Update'
```

`x-aiodrf-item-schema` is an aiodrf specification extension, not an OpenAPI
keyword. Its meaning depends on the annotated media type:

- `application/x-ndjson`: the JSON value on each nonempty line.
- `text/event-stream`: the JSON value decoded from a data-bearing event's joined
  `data` fields. Comments/heartbeats, `id`, `event` and `retry` are not items.

Clients parse the framing first. Swagger and client generators may ignore the
extension and see a string response; there is no SSE player in Swagger UI and
no generated streaming client. For plain-text SSE data use `OpenApiTypes.STR`,
not `StreamSchema`. For a JSON array (`StreamingArrayResponse`) use the item
serializer with `many=True`.

## Serializer and schema behavior

- Pass a DRF or aiodrf serializer class or instance. The annotation describes
  one item, so top-level `many=True` is rejected; nested list fields work as
  usual. `StreamSchema` is not a serializer for `serializer_class`, a request
  body, validation or representation.
- `MsgspecSerializer` and `PydanticSerializer` work through the existing typed
  schema extension. For a bare `msgspec.Struct` or Pydantic model, pass
  `StreamSchema(adapt(MySchema))` using `aiodrf.contrib.typed.adapt`; installing
  `[spectacular]` alone does not install either backend.
- Item components use spectacular's response resolution: output schemas,
  aliases, nested constraints and the exclusion of write-only fields.
  `COMPONENT_SPLIT_REQUEST` applies to request bodies as usual. Give distinct
  contracts distinct component names.
- The wrapper suppresses list and pagination wrapping, also on a viewset's
  `list` action. It changes neither runtime pagination nor filter parameters;
  disable inherited filters and pagination on a stream action that does not
  use them.
- An empty item serializer describes `{}`. JSON responses using the same item
  serializer reuse its component; SSE and NDJSON can share its stream component.

## Runtime remains application-owned

The annotation does not serialize items, execute views, consume a generator,
start the lifespan, select renderers, change headers or enforce permissions.
Serializers and schema hooks that touch live resources during introspection
still need the `swagger_fake_view` guard.

DRF negotiates content before the streaming response is returned, so annotating
`text/event-stream` does not make a JSON-only view accept that media type: a
client sending `Accept: text/event-stream` needs a renderer or negotiation
policy that accepts it. The example above works with `Accept: */*`, which a
browser's `EventSource` does not send. Authentication, source cleanup, proxy
buffering and the permissions of the schema views are unchanged.

Generate and validate offline:

```console
python manage.py spectacular --file schema.yml --validate --fail-on-warn
```

These annotations work with OpenAPI 3.0 and 3.1, with DRF, msgspec and Pydantic
items, aliases and nested constraints, separate request and response components,
custom status codes, shared components and protected documentation views.
Pagination is not described for list actions that stream, and invalid
annotations are reported as schema errors.

Design references: drf-spectacular's
[response annotations and serializer extensions](https://drf-spectacular.readthedocs.io/en/latest/customization.html)
and OpenAPI 3.1's
[media types](https://spec.openapis.org/oas/v3.1.1.html#media-type-object) and
[specification extensions](https://spec.openapis.org/oas/v3.1.1.html#specification-extensions).
