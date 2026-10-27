# SPDX-License-Identifier: BSD-3-Clause
"""Narrow sync/async bridges for legacy plugin imports of ``Users``.

Only exact imported ``Users`` bindings in dynamically loaded ``Pipe`` or
``Filter`` modules are replaced. The proxies bridge only bounded ID and email
lookups. Native application lookups and other plugin types retain their
original behavior.
"""

import asyncio
import importlib
import json
import os
import re
import sys
import types
from contextvars import ContextVar


_filter_async_user_lookups = ContextVar(
    "openatelier_filter_async_user_lookups", default=False
)
_FILTER_MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,128}", re.ASCII)
MAX_FILTER_MODEL_IDS_JSON_BYTES = 4096
MAX_FILTER_MODEL_IDS = 32


class FilterModelCompatConfigError(ValueError):
    """The explicit Filter model-lookup allowlist is invalid."""


def read_async_filter_model_ids():
    """Read the bounded exact-ID allowlist; unset means no new adaptation."""
    raw = os.environ.get("OPENATELIER_ASYNC_FILTER_MODEL_IDS")
    if raw is None or raw == "":
        return frozenset()
    try:
        if len(raw.encode("utf-8")) > MAX_FILTER_MODEL_IDS_JSON_BYTES:
            raise FilterModelCompatConfigError("Invalid async Filter model allowlist")

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise FilterModelCompatConfigError("Invalid async Filter model allowlist")
                result[key] = value
            return result

        values = json.loads(raw, object_pairs_hook=unique_object)
    except FilterModelCompatConfigError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise FilterModelCompatConfigError("Invalid async Filter model allowlist") from None
    if type(values) is not list or len(values) > MAX_FILTER_MODEL_IDS:
        raise FilterModelCompatConfigError("Invalid async Filter model allowlist")
    selected = set()
    for value in values:
        if (type(value) is not str or not value.isascii()
                or _FILTER_MODEL_ID_PATTERN.fullmatch(value) is None
                or value in selected):
            raise FilterModelCompatConfigError("Invalid async Filter model allowlist")
        selected.add(value)
    return frozenset(selected)


class _UsersLookupProxy:
    """Delegate Users APIs except for the bounded ID/email accessors."""

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

    def get_user_by_email(self, email):
        lookup = self._users.get_user_by_email
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return lookup(email)
        return asyncio.to_thread(lookup, email)

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


class _FilterUsersLookupProxy(_UsersLookupProxy):
    """Bridge only awaited ID/email lookups in adapted Filter handlers."""

    def get_user_by_id(self, id):
        lookup = self._users.get_user_by_id
        if _filter_async_user_lookups.get():
            return asyncio.to_thread(lookup, id)
        return lookup(id)

    def get_user_by_email(self, email):
        lookup = self._users.get_user_by_email
        if _filter_async_user_lookups.get():
            return asyncio.to_thread(lookup, email)
        return lookup(email)


class _FilterModelLookupProxy:
    """Expose selected blocking file/chat lookups as task-local awaitables."""

    def __init__(self, target, async_methods):
        self._target = target
        self._async_methods = frozenset(async_methods)

    def __getattr__(self, name):
        method = getattr(self._target, name)
        if name not in self._async_methods:
            return method

        def invoke(*args, **kwargs):
            if _filter_async_user_lookups.get():
                return asyncio.to_thread(method, *args, **kwargs)
            return method(*args, **kwargs)

        return invoke


class _FilterModuleView(types.ModuleType):
    """A module-local view which overrides one selected singleton export."""

    def __init__(self, wrapped, attribute, proxy):
        super().__init__(wrapped.__name__)
        self.__dict__["_wrapped"] = wrapped
        self.__dict__["_attribute"] = attribute
        self.__dict__["_proxy"] = proxy

    def __getattr__(self, name):
        if name == self.__dict__["_attribute"]:
            return self.__dict__["_proxy"]
        return getattr(self.__dict__["_wrapped"], name)


class _FilterImportlibView(_FilterModuleView):
    def __init__(self, wrapped, get_module_view):
        types.ModuleType.__init__(self, wrapped.__name__)
        self.__dict__["_wrapped"] = wrapped
        self.__dict__["_get_module_view"] = get_module_view

    def __getattr__(self, name):
        if name == "import_module":
            return self.import_module
        return getattr(self.__dict__["_wrapped"], name)

    def import_module(self, name, package=None):
        result = importlib.import_module(name, package)
        if name in ("open_webui.models.files", "open_webui.models.chats"):
            return self.__dict__["_get_module_view"](name, result)
        return result


def adapt_filter_module(module, users, files=None, chats=None, *, adapt_models=False):
    """Adapt exact module-level SDK bindings in one loaded Filter."""
    if getattr(module, "Filter", None) is None:
        return False
    marker = "__openatelier_filter_user_lookup_compat__"
    model_marker = "__openatelier_filter_model_lookup_compat__"
    if (module.__dict__.get(marker) is True
            and (not adapt_models or module.__dict__.get(model_marker) is True)):
        return True
    targets = {
        "open_webui.models.files": ("Files", files, ("get_file_by_id",)),
        "open_webui.models.chats": ("Chats", chats, ("get_chat_by_id_and_user_id",)),
    }
    views = {}
    replacements = {}
    proxies = {}

    def proxy_for(singleton, methods):
        proxy = proxies.get(id(singleton))
        if proxy is None:
            proxy = _FilterModelLookupProxy(singleton, methods)
            proxies[id(singleton)] = proxy
            replacements[id(singleton)] = proxy
        return proxy

    def target_view(module_name, real_module):
        if module_name in views:
            return views[module_name]
        attribute, _, methods = targets[module_name]
        singleton = getattr(real_module, attribute, None)
        if singleton is None:
            return real_module
        proxy = proxy_for(singleton, methods)
        view = _FilterModuleView(real_module, attribute, proxy)
        views[module_name] = view
        return view

    if adapt_models:
        for module_name, (attribute, supplied, methods) in targets.items():
            real_module = sys.modules.get(module_name)
            singleton = getattr(real_module, attribute, None) if real_module else None
            if supplied is not None and singleton is supplied:
                proxy_for(supplied, methods)
            if real_module is not None:
                for name, value in list(module.__dict__.items()):
                    if value is real_module:
                        module.__dict__[name] = target_view(module_name, real_module)
                    elif singleton is not None and value is singleton:
                        module.__dict__[name] = proxy_for(singleton, methods)

    users_adapted = False
    if users is not None:
        users_proxy = _FilterUsersLookupProxy(users)
        for name, value in list(module.__dict__.items()):
            if value is users:
                module.__dict__[name] = users_proxy
                users_adapted = True

    importlib_module = sys.modules.get("importlib")
    has_importlib_binding = False
    if adapt_models and importlib_module is not None:
        for value in module.__dict__.values():
            if value is importlib_module or value is importlib_module.import_module:
                has_importlib_binding = True
                break
    if has_importlib_binding:
        def get_module_view(module_name, real_module=None):
            if module_name not in targets:
                return real_module
            if real_module is None:
                real_module = importlib.import_module(module_name)
            return target_view(module_name, real_module)

        importlib_view = _FilterImportlibView(importlib_module, get_module_view)
        for name, value in list(module.__dict__.items()):
            if value is importlib_module:
                module.__dict__[name] = importlib_view
            elif value is importlib_module.import_module:
                module.__dict__[name] = importlib_view.import_module

    model_adapted = bool(adapt_models and (replacements or views or has_importlib_binding))
    adapted = bool(users_adapted or module.__dict__.get(marker) is True or model_adapted)
    module.__dict__[marker] = adapted
    if model_adapted:
        module.__dict__[model_marker] = True
    # The existing loader returns a Filter instance, not its source module.
    # A private marker on this plugin's class makes the dispatcher recognize
    # that returned instance without changing any application-wide class.
    setattr(module.Filter, marker, adapted)
    return adapted


def set_filter_async_user_lookup_mode(async_mode):
    """Set per-call lookup mode and return its reset token."""
    return _filter_async_user_lookups.set(async_mode)


def reset_filter_async_user_lookup_mode(token):
    _filter_async_user_lookups.reset(token)


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
