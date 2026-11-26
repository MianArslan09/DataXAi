"""Private cleanup policy shared by source adapters and snapshot custody."""

from contextlib import contextmanager


def close_resources(*callbacks, primary_error=None):
    """Attempt every cleanup; raise its first failure only without a primary error.

    GeneratorExit requests cleanup rather than reporting a processing failure.
    Cleanup failures on an explicit generator close must therefore remain visible.
    """
    if isinstance(primary_error, GeneratorExit):
        primary_error = None
    first_error = primary_error
    for callback in callbacks:
        try:
            callback()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    if primary_error is None and first_error is not None:
        raise first_error


@contextmanager
def cleanup_on_exit(*callbacks):
    try:
        yield
    except BaseException as exc:
        close_resources(*callbacks, primary_error=exc)
        raise
    else:
        close_resources(*callbacks)
