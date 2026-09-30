# Streaming through Nginx

`EventStreamResponse` and `StreamingResponse` accept Django's response
`headers`. Proxy buffering is a deployment choice; it needs no middleware patch
and no change to other responses.

```python
from aiodrf.response import EventStreamResponse, StreamingResponse

response = EventStreamResponse(events(), headers={"X-Accel-Buffering": "no"})
export = StreamingResponse(rows(), headers={"X-Accel-Buffering": "no"})
```

Or configure a location dedicated to streaming:

```nginx
location /api/events/ {
    proxy_pass http://application;
    proxy_http_version 1.1;
    proxy_buffering off;
}
```

Do not set `proxy_ignore_headers X-Accel-Buffering` if the application is to
control buffering. An explicit `yes` enables buffering even where the location
turns it off. Nginx consumes this header, so its absence from the client
response does not show it was ignored. See the
[Nginx buffering contract](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering).

Buffering, caching and compression are separate policies. aiodrf adds no
default header: SSE keeps its `Cache-Control: no-cache`, and NDJSON gets none
because it streams. Choose compression and proxy read timeouts for the
application, heartbeat intervals included. gzip does not keep the first-item
latency of an uncompressed response, and `proxy_buffering on` does not hold
every short chunk.

## Tested behaviour

Server-Sent Events and NDJSON responses were tested through Nginx with
buffering off, buffering on, buffering on with `X-Accel-Buffering` ignored, and
gzip with buffering off and on, each with the application sending no header,
`no` and `yes`. In every case the decoded payload, content type and cache
headers are correct and the producer is closed at the end. Only unbuffered
locations guarantee that an item reaches the client before the producer
finishes. Compression applies only when the client negotiates it.

These tests use local HTTP/1.1, plus a separate HTTP/2 test with TLS for client
disconnects (see [deployment behaviour](deployment-validation.md#streaming-through-nginx)).
They say nothing about a production TLS setup, CDN or ingress controller;
test your deployed chain separately.
