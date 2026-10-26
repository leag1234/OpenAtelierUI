import inspect
import logging

from open_webui.utils.plugin import load_function_module_by_id
from open_webui.utils.plugin_user_compat import (
    reset_filter_async_user_lookup_mode,
    set_filter_async_user_lookup_mode,
)
from open_webui.models.functions import Functions
from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


def get_sorted_filter_ids(model: dict, request=None):
    def get_filter_module(function_id):
        if request is not None:
            function_modules = request.app.state.FUNCTIONS
            if function_id in function_modules:
                return function_modules[function_id]

        function_module, _, _ = load_function_module_by_id(function_id)
        if request is not None:
            request.app.state.FUNCTIONS[function_id] = function_module
        return function_module

    def get_default_priority(function_module):
        valves_type = getattr(function_module, "Valves", None)
        if valves_type is None:
            return 0

        fields = getattr(valves_type, "model_fields", None)
        if fields is not None:
            priority_field = fields.get("priority")
            if priority_field is None or priority_field.is_required():
                return 0
            return priority_field.default

        class_default = vars(valves_type).get("priority")
        if class_default is not None and not isinstance(class_default, property):
            return class_default

        try:
            priority_parameter = inspect.signature(valves_type).parameters.get(
                "priority"
            )
        except (TypeError, ValueError):
            priority_parameter = None
        if (
            priority_parameter is not None
            and priority_parameter.default is not inspect.Parameter.empty
        ):
            return priority_parameter.default
        return 0

    def get_priority(function_id):
        valves = Functions.get_function_valves_by_id(function_id)
        if valves is not None and "priority" in valves:
            return valves["priority"]
        function_module = get_filter_module(function_id)
        return get_default_priority(function_module)

    filter_ids = [function.id for function in Functions.get_global_filter_functions()]
    if "info" in model and "meta" in model["info"]:
        filter_ids.extend(model["info"]["meta"].get("filterIds", []))
        filter_ids = list(set(filter_ids))

    enabled_filter_ids = [
        function.id
        for function in Functions.get_functions_by_type("filter", active_only=True)
    ]

    filter_ids = [fid for fid in filter_ids if fid in enabled_filter_ids]
    filter_ids.sort(key=get_priority)
    return filter_ids


async def process_filter_functions(
    request, filter_functions, filter_type, form_data, extra_params
):
    skip_files = None

    for function in filter_functions:
        filter = function
        filter_id = function.id
        if not filter:
            continue

        if filter_id in request.app.state.FUNCTIONS:
            function_module = request.app.state.FUNCTIONS[filter_id]
        else:
            function_module, _, _ = load_function_module_by_id(filter_id)
            request.app.state.FUNCTIONS[filter_id] = function_module

        # Prepare handler function
        handler = getattr(function_module, filter_type, None)
        if not handler:
            continue

        # Check if the function has a file_handler variable
        if filter_type == "inlet" and hasattr(function_module, "file_handler"):
            skip_files = function_module.file_handler

        # Apply valves to the function
        if hasattr(function_module, "valves") and hasattr(function_module, "Valves"):
            valves = Functions.get_function_valves_by_id(filter_id)
            function_module.valves = function_module.Valves(
                **(valves if valves else {})
            )

        try:
            # Prepare parameters
            sig = inspect.signature(handler)

            params = {"body": form_data}
            if filter_type == "stream":
                params = {"event": form_data}

            params = params | {
                k: v
                for k, v in {
                    **extra_params,
                    "__id__": filter_id,
                }.items()
                if k in sig.parameters
            }

            # Handle user parameters
            if "__user__" in sig.parameters:
                if hasattr(function_module, "UserValves"):
                    try:
                        params["__user__"]["valves"] = function_module.UserValves(
                            **Functions.get_user_valves_by_id_and_user_id(
                                filter_id, params["__user__"]["id"]
                            )
                        )
                    except Exception as e:
                        log.exception(f"Failed to get user values: {e}")

            # Execute adapted Filters with a task-local lookup mode. The
            # default remains synchronous for constructors and native callers.
            adapted_filter = getattr(
                function_module,
                "__openatelier_filter_user_lookup_compat__",
                False,
            )
            lookup_mode_token = None
            if adapted_filter:
                lookup_mode_token = set_filter_async_user_lookup_mode(
                    inspect.iscoroutinefunction(handler)
                )
            try:
                if inspect.iscoroutinefunction(handler):
                    form_data = await handler(**params)
                else:
                    form_data = handler(**params)
            finally:
                if lookup_mode_token is not None:
                    reset_filter_async_user_lookup_mode(lookup_mode_token)

        except Exception as e:
            log.debug(f"Error in {filter_type} handler {filter_id}: {e}")
            raise e

    # Handle file cleanup for inlet
    if skip_files and "files" in form_data.get("metadata", {}):
        del form_data["files"]
        del form_data["metadata"]["files"]

    return form_data, {}
