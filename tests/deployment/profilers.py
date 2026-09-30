"""Opt-in attribution in a disposable worker; never active in the soak path."""

import gc
import os

from tests.live.resource_workload import process_metrics


def start_profile(mode):
    if mode == "cpu":
        import yappi

        yappi.set_clock_type("cpu")
        yappi.start()
    elif mode == "memory":
        import tracemalloc

        tracemalloc.start(5)
    else:
        raise ValueError(f"Unknown profiling mode: {mode}")


def allocations(snapshot):
    return [
        {"trace": str(stat.traceback), "size": stat.size, "count": stat.count}
        for stat in snapshot.statistics("lineno")[:30]
    ]


def stop_profile(mode):
    if mode == "cpu":
        import yappi

        yappi.stop()
        functions = sorted(
            yappi.get_func_stats(), key=lambda stat: stat.tsub, reverse=True
        )
        return {
            "mode": mode,
            "bridges": [
                {
                    "function": stat.full_name,
                    "calls": stat.ncall,
                    "own_seconds": stat.tsub,
                }
                for stat in functions
                if "/asgiref/sync.py:" in stat.full_name
            ],
            "cpu_top": [
                {
                    "function": stat.full_name,
                    "calls": stat.ncall,
                    "own_seconds": stat.tsub,
                    "total_seconds": stat.ttot,
                }
                for stat in functions[:50]
            ],
            "serialization": [
                {
                    "function": stat.full_name,
                    "calls": stat.ncall,
                    "own_seconds": stat.tsub,
                    "total_seconds": stat.ttot,
                }
                for stat in functions
                if any(
                    part in stat.full_name
                    for part in (
                        "/aiodrf/aio.py:",
                        "/aiodrf/contrib/builtin/concurrent.py:",
                        "/rest_framework/serializers.py:",
                    )
                )
            ],
            "threads": [
                {"name": stat.name, "cpu_seconds": stat.ttot}
                for stat in yappi.get_thread_stats()
            ],
        }
    if mode == "memory":
        import tracemalloc

        current, peak = tracemalloc.get_traced_memory()
        before = allocations(tracemalloc.take_snapshot())
        # Diagnostic only; no forced collection in normal app/soak execution.
        collected = gc.collect()
        after, _ = tracemalloc.get_traced_memory()
        retained = allocations(tracemalloc.take_snapshot())
        tracemalloc.stop()
        return {
            "mode": mode,
            "traced_current_bytes": current,
            "traced_peak_bytes": peak,
            "after_gc_bytes": after,
            "gc_collected": collected,
            "rss_after_gc": process_metrics(os.getpid())["rss_bytes"],
            "before_gc_top": before,
            "after_gc_top": retained,
        }
    raise ValueError(f"Unknown profiling mode: {mode}")
