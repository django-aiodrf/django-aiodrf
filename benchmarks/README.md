# Self-hosted performance validation

This directory specifies future performance checks on a dedicated runner.
There is no active performance release gate. Functional, concurrency and
resource-lifecycle tests remain required independently of benchmark results.
Historical measurements and cross-framework comparisons are not distributed
with this repository.

## Runner requirements

Use a reserved Linux host with a recorded CPU model, governor, memory budget,
kernel, interpreter build and service image digests. Keep load generation
separate from server CPU allocation. Run trusted source only: pull requests
from forks must never execute on a persistent self-hosted runner with secrets.
Create a disposable database and output directory for each run; clean up only
resources whose ownership the harness can verify.

## Scenario matrix

| Scenario | Required variants | Correctness checks |
| --- | --- | --- |
| Raw response | Small and large JSON, no database | Status, headers and decoded payload |
| Serialization | Scalar, nested and collection fields; cold and warm plans | DRF output/errors; custom-field fallback; object isolation |
| Optional optimizations | Compiler, field cache, recursive copy and batching separately, then combined | Default-disabled path and equivalent data access |
| Database | List/detail/create, relation loading, pagination | Query count, result order, permissions and writes |
| I/O concurrency | Controlled async HTTP; database plus HTTP | Concurrency limits, timeouts, cancellation and loop delay |
| Streaming | SSE/NDJSON, slow clients and disconnects | Producer closure, backpressure, bounded retained state |
| Runtime | Supported ordinary and free-threaded Python | Actual GIL state, warnings and dependency compatibility |
| Deployment | Uvicorn + Nginx + PostgreSQL | Worker/pool budgets and equivalent proxy configuration |

## Measurement protocol

Build the baseline and candidate wheels from identified source commits. Use
the same interpreter, resolved dependencies, settings, dataset and resources
for both. Keep installation outside timed regions. Alternate baseline/candidate
process order across independent starts; warm each process and retain individual
samples. A first release uses repeated identical-wheel runs to characterize
noise, not a fabricated previous-release baseline.

Record throughput, latency distribution, errors, CPU, RSS, loop delay, database
connections and query counts where applicable. Separate profiler runs from
uninstrumented timing. Store wheel hashes, dependency versions, commands,
environment metadata and raw samples alongside the report in a uniquely named
results directory in the future benchmark repository.

## Release policy prerequisites

Do not enable a blocking percentage threshold before runner calibration.
Determine the repeatability envelope per scenario, then choose and document a
material regression threshold outside that envelope using repeated A/A and
known-regression trials. Evaluate scenarios independently. Missing samples,
failed correctness checks or excessive noise must not be reported as a pass.
Enabling publication blocking and an exception-approval environment requires
a separate reviewed workflow change and repository configuration.

For current functional checks, see the
[deployment validation guide](../docs/guides/deployment-validation.md) and
[release procedure](../RELEASE.md).
