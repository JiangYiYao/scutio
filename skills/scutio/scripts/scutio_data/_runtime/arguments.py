"""Call binding diagnostics derived from signatures, without argument values."""

from __future__ import annotations

import inspect
from functools import partial


class InvalidArguments(TypeError):
    """A call does not bind to its signature; safe to expose in a batch result."""


def validate_arguments(function, args=(), kwargs=None, *, signature=None):
    kwargs = kwargs or {}
    if isinstance(function, partial):
        return validate_arguments(
            function.func, function.args + args, {**(function.keywords or {}), **kwargs}
        )
    if signature is None:
        try:
            # A wrapper may supply the underlying function's required arguments.
            # Only operation() explicitly validates its saved domain signature.
            signature = inspect.signature(function, follow_wrapped=False)
        except (TypeError, ValueError):
            # Some native callables have no inspectable signature.
            return
    try:
        signature.bind(*args, **kwargs)
    except TypeError:
        required, optional = [], []
        for parameter in signature.parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            target = required if parameter.default is parameter.empty else optional
            target.append(parameter.name)
        name = getattr(function, "__name__", type(function).__name__)
        # Neither the binding exception, supplied keywords, defaults nor
        # annotations are safe to print: any of them may contain credentials.
        raise InvalidArguments(
            f"{name}: invalid arguments; required parameters: {', '.join(required) or '(none)'}; "
            f"optional parameters: {', '.join(optional) or '(none)'}. "
            "Check parameter names and positional/keyword placement with inspect.signature()."
        ) from None
