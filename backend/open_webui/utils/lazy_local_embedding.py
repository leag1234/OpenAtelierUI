# SPDX-License-Identifier: BSD-3-Clause
"""Opt-in first-use wrapper for the existing local embedding factory."""

import os
import threading


LAZY_LOCAL_EMBEDDING_STARTUP_ENV = "OPENATELIER_LAZY_LOCAL_EMBEDDING_STARTUP"
_UNINITIALIZED = object()


def lazy_local_embedding_startup_enabled():
    """Return true only for the explicit opt-in value."""
    return os.environ.get(LAZY_LOCAL_EMBEDDING_STARTUP_ENV) == "1"


class DeferredLocalEmbedding:
    """Initialize one captured local encoder on the first encode call.

    Initialization is single-flight. A factory exception or ``None`` result is
    retained and reported on every later call; the factory is never retried.
    Warm encode calls are forwarded directly and are not serialized by the
    initialization lock.
    """

    def __init__(self, factory, engine, model, auto_update):
        self._factory = factory
        self._engine = engine
        self._model_name = model
        self._auto_update = auto_update
        self._lock = threading.Lock()
        self._instance = _UNINITIALIZED
        self._failure = None

    def _get_instance(self):
        if self._instance is _UNINITIALIZED and self._failure is None:
            with self._lock:
                if self._instance is _UNINITIALIZED and self._failure is None:
                    try:
                        instance = self._factory(
                            self._engine, self._model_name, self._auto_update
                        )
                    except Exception as error:
                        self._failure = error
                    else:
                        if instance is None:
                            self._failure = RuntimeError(
                                "The configured local embedding factory returned no encoder"
                            )
                        else:
                            self._instance = instance

        if self._failure is not None:
            raise self._failure
        return self._instance

    def encode(self, *args, **kwargs):
        """Forward the first and subsequent encode calls unchanged."""
        return self._get_instance().encode(*args, **kwargs)
