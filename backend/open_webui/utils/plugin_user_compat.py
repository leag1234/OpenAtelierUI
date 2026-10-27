# SPDX-License-Identifier: BSD-3-Clause
"""Narrow sync/async bridges for legacy plugin imports of ``Users``.

Only exact imported ``Users`` bindings in dynamically loaded ``Pipe`` or
``Filter`` modules are replaced. The proxies bridge only bounded ID and email
lookups. Native application lookups and other plugin types retain their
original behavior.
"""

import asyncio
import builtins
import importlib
import types
from contextvars import ContextVar


_filter_async_user_lookups = ContextVar(
    "openatelier_filter_async_user_lookups", default=False
)


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
    """A module-local view which overrides only selected singleton exports."""

    def __init__(self, wrapped, overrides=None, lazy_modules=None):
        super().__init__(wrapped.__name__)
        self.__dict__["_wrapped"] = wrapped
        self.__dict__["_overrides"] = dict(overrides or {})
        self.__dict__["_lazy_modules"] = lazy_modules or {}

    def __getattr__(self, name):
        overrides = self.__dict__["_overrides"]
        if name in overrides:
            return overrides[name]
        lazy_path = self.__dict__["_lazy_modules"].get(name)
        if lazy_path is not None:
            value = self.__dict__["_view_factory"](lazy_path)
            overrides[name] = value
            return value
        return getattr(self.__dict__["_wrapped"], name)


class _FilterImportlibView(_FilterModuleView):
    def __init__(self, wrapped, get_module_view):
        super().__init__(wrapped)
        self.__dict__["_get_module_view"] = get_module_view

    def import_module(self, name, package=None):
        result = importlib.import_module(name, package)
        if name in ("open_webui.models.files", "open_webui.models.chats"):
            return self.__dict__["_get_module_view"](name, result)
        return result


def adapt_filter_module(module, users, files=None, chats=None):
    """Adapt only selected direct/dynamic model imports in a loaded Filter."""
    if getattr(module, "Filter", None) is None:
        return False
    replacements = {}
    targets = {
        "open_webui.models.files": ("Files", files, ("get_file_by_id",)),
        "open_webui.models.chats": ("Chats", chats, ("get_chat_by_id_and_user_id",)),
    }
    module_views, real_module_views = {}, {}
    proxy_by_id = {}
    has_target_binding = files is not None or chats is not None

    def proxy_for(singleton, methods):
        proxy = proxy_by_id.get(id(singleton))
        if proxy is None:
            proxy = _FilterModelLookupProxy(singleton, methods)
            proxy_by_id[id(singleton)] = proxy
            replacements[id(singleton)] = proxy
        return proxy

    if users is not None:
        replacements[id(users)] = _FilterUsersLookupProxy(users)

    def target_view(module_name, real_module=None):
        if module_name in module_views:
            return module_views[module_name]
        attribute, singleton, methods = targets[module_name]
        if real_module is None:
            real_module = importlib.import_module(module_name)
        if singleton is None:
            singleton = getattr(real_module, attribute)
        proxy = proxy_for(singleton, methods)
        view = _FilterModuleView(real_module, {attribute: proxy})
        module_views[module_name] = view
        real_module_views[id(real_module)] = view
        return view

    # Detect direct singleton/module imports without forcing unrelated model
    # modules to import for filters which never use Files or Chats.
    for module_name, (attribute, singleton, methods) in targets.items():
        if singleton is not None:
            proxy_for(singleton, methods)
        for value in module.__dict__.values():
            if isinstance(value, types.ModuleType) and value.__name__ == module_name:
                has_target_binding = True
                target_view(module_name, value)
            elif value is not None and not isinstance(value, type) and \
                    type(value).__module__ == module_name:
                has_target_binding = True
                proxy_for(value, methods)

    # Direct Users aliases retain the previously qualified bridge.
    if users is not None:
        replacements[id(users)] = _FilterUsersLookupProxy(users)

    # Replace values already imported at module scope, including aliases and
    # module objects, without changing the process-wide import cache.
    for name, value in list(module.__dict__.items()):
        if id(value) in replacements:
            module.__dict__[name] = replacements[id(value)]
        elif id(value) in real_module_views:
            module.__dict__[name] = real_module_views[id(value)]

    has_importlib_binding = any(
        value is importlib or value is importlib.import_module
        for value in module.__dict__.values()
    )
    target_paths = {name: name.rsplit(".", 1)[1] for name in targets}
    if has_target_binding or has_importlib_binding:
        def get_module_view(module_name, real_module=None):
            return target_view(module_name, real_module)

        importlib_view = _FilterImportlibView(importlib, get_module_view)

        def package_view(name):
            models_view = _FilterModuleView(
                importlib.import_module("open_webui.models"),
                lazy_modules={alias: path for path, alias in target_paths.items()},
            )
            models_view.__dict__["_view_factory"] = get_module_view
            if name == "open_webui.models":
                return models_view
            return _FilterModuleView(
                importlib.import_module("open_webui"), {"models": models_view}
            )

        real_import = module.__dict__.get("__builtins__", builtins)
        real_import = real_import.get("__import__", builtins.__import__) if isinstance(real_import, dict) else real_import.__import__

        def local_import(name, globals=None, locals=None, fromlist=(), level=0):
            result = real_import(name, globals, locals, fromlist, level)
            fromlist = fromlist or ()
            if level == 0 and "*" in fromlist:
                return result
            if level == 0 and name in targets:
                return target_view(name, result) if fromlist else package_view("open_webui")
            if level == 0 and name == "open_webui.models":
                return package_view("open_webui.models")
            if level == 0 and name == "open_webui":
                return package_view("open_webui")
            if level == 0 and name == "importlib":
                return importlib_view
            return result

        original_builtins = module.__dict__.get("__builtins__", builtins.__dict__)
        builtin_map = dict(original_builtins if isinstance(original_builtins, dict) else vars(original_builtins))
        builtin_map["__import__"] = local_import
        module.__dict__["__builtins__"] = builtin_map
        for name, value in list(module.__dict__.items()):
            if value is importlib:
                module.__dict__[name] = importlib_view
            elif value is importlib.import_module:
                module.__dict__[name] = importlib_view.import_module

    marker = "__openatelier_filter_user_lookup_compat__"
    adapted = bool(replacements or module_views or has_importlib_binding)
    module.__dict__[marker] = adapted
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
