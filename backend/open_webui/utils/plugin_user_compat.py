# SPDX-License-Identifier: BSD-3-Clause
"""Narrow sync/async bridge for legacy pipe imports of ``Users``.

Only the exact imported ``Users`` binding in a dynamically loaded ``Pipe``
module is replaced. Native application lookups and other plugin types retain
their original behavior.
"""

import asyncio


class _UsersLookupProxy:
    """Delegate Users APIs except for the one adapted accessor."""

    def __init__(self, users):
        self._users = users

    def get_user_by_id(self, id):
        lookup = self._users.get_user_by_id
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # The chat dispatcher runs adapted sync pipes in a worker, so this
            # returns the exact native UserModel/None/error result.
            return lookup(id)
        # Async pipes can await this coroutine; the blocking legacy lookup
        # executes in a worker thread and resolves to UserModel or None.
        return asyncio.to_thread(lookup, id)

    def __getattr__(self, name):
        return getattr(self._users, name)


def adapt_pipe_module(module, users):
    """Replace direct bindings to the ``Users`` singleton in Pipe modules.

    Aliases are handled by binding identity. Dynamic imports, re-exports,
    copied functions and other internal symbols are outside this prototype.
    """
    if getattr(module, "Pipe", None) is None:
        return False
    bindings = [name for name, value in module.__dict__.items() if value is users]
    if not bindings:
        return False
    proxy = _UsersLookupProxy(users)
    for name in bindings:
        module.__dict__[name] = proxy
    module.__dict__["__openatelier_user_lookup_compat__"] = True
    return True


async def execute_pipe(pipe, params, module_lookup):
    """Execute adapted synchronous pipes off-loop; preserve other dispatch."""
    import inspect

    if inspect.iscoroutinefunction(pipe):
        return await pipe(**params)

    owner = getattr(pipe, "__self__", None)
    module_name = (
        owner.__class__.__module__
        if owner is not None
        else getattr(pipe, "__module__", None)
    )
    module = module_lookup(module_name)
    adapted = module is not None and getattr(
        module, "__openatelier_user_lookup_compat__", False
    )
    if adapted:
        return await asyncio.to_thread(pipe, **params)
    return pipe(**params)
