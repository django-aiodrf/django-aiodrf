def test_dependent_caches_are_dropped_with_the_classification():
    from aiodrf.utils import (
        _pure,
        _transparent,
        bridge_base,
        class_cache,
        depends_on_classification,
    )

    calls = []

    @depends_on_classification
    @class_cache
    def decided(cls):
        calls.append(cls)
        return len(calls)

    class Subject:
        pass

    for declare in (
        _pure.changed,
        lambda: bridge_base(type("Base", (), {})),
        lambda: _transparent(type("Mixin", (), {})),
    ):
        first = decided(Subject)
        assert decided(Subject) == first
        declare()
        assert decided(Subject) == first + 1
