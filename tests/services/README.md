# Local test services

`compose.yaml` defines disposable PostgreSQL, Redis and MongoDB services for
explicit integration sessions. Inspect its ports and credentials before running
`docker compose -f tests/services/compose.yaml up -d --wait`. Do not connect these
tests to an application database or replace an existing service on the same port.

Run the corresponding `tests_postgres`, `ecosystem`, `ecosystem_mongodb` or
`native_db` nox session. Deployment tests create and remove only their own uniquely
named databases. Service startup and shutdown are operator actions; nox does not
stop unrelated services. These local credentials are test fixtures, not production
configuration. See the [deployment guide](../../docs/guides/deployment-validation.md).

`cache-topologies.yaml` is a separate loopback-only Redis/Valkey Sentinel and
Cluster project. Follow [the topology test procedure](cache-topologies.md);
it includes a deliberate failover and must never target production.

`opensearch.yaml` starts a separate security-disabled local OpenSearch server.
See [the OpenSearch guide](../../docs/guides/async-nosql.md#opensearch)
for native document tests, 2.x/3.x profiles and the isolated vendor warning.
