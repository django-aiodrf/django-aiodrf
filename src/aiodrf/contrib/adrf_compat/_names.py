"""
What makes a method an adrf action, shared by the codemod (which must not
need Django settings) and the system check ``aiodrf.W006``.
"""

from collections.abc import Sequence


def takes_request(parameters: Sequence[str]) -> bool:
    """
    Whether a method with ``parameters`` (its names after ``self``, with
    ``*`` and ``**`` prefixes) has an action's signature: ``request`` first,
    or only ``*args, **kwargs``, as adrf declares its actions. A
    serializer-shaped ``acreate(self, validated_data)`` on a view is not an
    action.
    """
    if parameters and parameters[0] == "request":
        return True
    return (
        len(parameters) == 2
        and parameters[0].startswith("*")
        and parameters[1].startswith("**")
    )
