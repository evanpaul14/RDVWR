"""A shared monotonic budget for nested upstream requests and retries."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
import time
import requests

_deadline = ContextVar('upstream_deadline', default=None)


def remaining(timeout=10):
    deadline = _deadline.get()
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise requests.exceptions.Timeout('Upstream request budget exhausted')
    return min(timeout, left)


@contextmanager
def budget(seconds):
    parent = _deadline.get()
    deadline = time.monotonic() + seconds
    token = _deadline.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def budgeted(fn):
    sig = signature(fn)
    @wraps(fn)
    def wrapped(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        seconds = bound.arguments.get('timeout', bound.arguments.get('kwargs', {}).get('timeout', 10))
        with budget(seconds):
            return fn(*args, **kwargs)
    return wrapped


def request(call, *args, **kwargs):
    kwargs['timeout'] = remaining(kwargs.get('timeout', 10))
    return call(*args, **kwargs)


def backoff(seconds):
    left = remaining(seconds)
    if left < seconds:
        raise requests.exceptions.Timeout('Insufficient upstream budget for retry')
    time.sleep(seconds)
