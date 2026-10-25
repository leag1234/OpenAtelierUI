import copy
import hashlib
import inspect
import json
import logging
import re
import aiohttp
import asyncio
import yaml

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from typing import (
    Any,
    Awaitable,
    Callable,
    get_type_hints,
    get_args,
    get_origin,
    Dict,
    List,
    Tuple,
    Union,
    Optional,
    Type,
)
from functools import update_wrapper, partial


from fastapi import Request
from pydantic import BaseModel, Field, create_model

from langchain_core.utils.function_calling import (
    convert_to_openai_function as convert_pydantic_model_to_openai_function_spec,
)


from open_webui.models.tools import Tools
from open_webui.models.users import UserModel
from open_webui.utils.plugin import load_tool_module_by_id
from open_webui.env import AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA

log = logging.getLogger(__name__)


_NAMED_SERVER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _named_server_config(connection: dict) -> tuple[bool, Optional[str]]:
    """Return (named-mode-attempt, valid ID) for the measured explicit format."""
    if not isinstance(connection, dict):
        return False, None
    info = connection.get("info")
    has_info_id = isinstance(info, dict) and "id" in info
    attempted = "spec_type" in connection or has_info_id
    if not attempted:
        return False, None
    if connection.get("type") != "openapi" or connection.get("spec_type") != "url":
        return True, None
    if not isinstance(info, dict):
        return True, None
    server_id = info.get("id")
    if (
        not isinstance(server_id, str)
        or not server_id.isascii()
        or not _NAMED_SERVER_ID.fullmatch(server_id)
        or server_id.isdecimal()
    ):
        return True, None
    return True, server_id


def _tool_server_connection_binding(connection: dict) -> tuple:
    """Non-secret config identity kept only in the in-memory server cache."""
    config = connection.get("config") if isinstance(connection, dict) else None
    if not isinstance(config, dict):
        config = {}
    _attempted, server_id = _named_server_config(connection)
    binding_fields = (
        connection.get("url") if isinstance(connection, dict) else None,
        connection.get("path", "openapi.json") if isinstance(connection, dict) else None,
        connection.get("type") if isinstance(connection, dict) else None,
        connection.get("spec_type") if isinstance(connection, dict) else None,
        server_id,
        config.get("enable") is True,
    )
    # Keep only a digest in application state; URLs can themselves contain secrets.
    encoded = json.dumps(binding_fields, ensure_ascii=True, separators=(",", ":"))
    return (hashlib.sha256(encoded.encode("utf-8")).hexdigest(),)


def named_tool_server_catalog_ids(
    connections: list, servers: list
) -> tuple[dict[int, str], set[int]]:
    """Map valid, unique, fresh named servers by original connection index."""
    named_indices: set[int] = set()
    counts: dict[str, int] = {}
    valid_ids: dict[int, str] = {}
    for index, connection in enumerate(connections):
        attempted, server_id = _named_server_config(connection)
        if not attempted:
            continue
        named_indices.add(index)
        config = connection.get("config") if isinstance(connection, dict) else None
        if isinstance(config, dict) and config.get("enable") is True and server_id is not None:
            valid_ids[index] = server_id
            counts[server_id] = counts.get(server_id, 0) + 1

    catalog_ids: dict[int, str] = {}
    for index, server_id in valid_ids.items():
        if counts[server_id] != 1:
            continue
        matches = [server for server in servers if server.get("idx") == index]
        if len(matches) != 1:
            continue
        if matches[0].get("connection_binding") != _tool_server_connection_binding(connections[index]):
            continue
        catalog_ids[index] = f"server:{server_id}"
    return catalog_ids, named_indices


def named_tool_server_read_allowed(connection: dict, user: UserModel) -> bool:
    """Check only the measured exact user/read grant for a named server."""
    role = getattr(user, "role", None)
    if role == "admin":
        return True
    user_id = getattr(user, "id", None)
    if role != "user" or not isinstance(user_id, str) or not user_id.strip():
        return False
    config = connection.get("config") if isinstance(connection, dict) else None
    grants = config.get("access_grants") if isinstance(config, dict) else None
    if not isinstance(grants, list) or len(grants) != 1:
        return False
    grant = grants[0]
    if not isinstance(grant, dict) or set(grant) != {
        "principal_type",
        "principal_id",
        "permission",
    }:
        return False
    return (
        grant.get("principal_type") == "user"
        and grant.get("principal_id") == user_id
        and isinstance(grant.get("principal_id"), str)
        and bool(grant.get("principal_id").strip())
        and grant.get("permission") == "read"
    )


def resolve_tool_server_reference(
    tool_id: str, connections: list, servers: list, user: UserModel
) -> Optional[tuple[dict, dict]]:
    """Resolve canonical named IDs or preserve legacy numeric IDs safely."""
    if not isinstance(tool_id, str) or not tool_id.startswith("server:"):
        return None
    catalog_ids, named_indices = named_tool_server_catalog_ids(connections, servers)
    named_by_id = {tool_id_value: index for index, tool_id_value in catalog_ids.items()}
    named_index = named_by_id.get(tool_id)
    if named_index is not None:
        if not named_tool_server_read_allowed(connections[named_index], user):
            return None
        matches = [server for server in servers if server.get("idx") == named_index]
        if len(matches) != 1:
            return None
        return connections[named_index], matches[0]

    suffix = tool_id[len("server:"):]
    if not suffix.isascii() or not suffix.isdecimal() or len(suffix) > 10:
        return None
    index = int(suffix)
    if index >= len(connections) or index in named_indices:
        return None
    matches = [server for server in servers if server.get("idx") == index]
    if len(matches) != 1:
        return None
    return connections[index], matches[0]


def is_reserved_named_server_reference(tool_id: str, connections: list) -> bool:
    """Keep named-server selections out of the local-plugin fallback path."""
    if not isinstance(tool_id, str) or not tool_id.startswith("server:"):
        return False
    suffix = tool_id[len("server:") :]
    for index, connection in enumerate(connections):
        attempted, server_id = _named_server_config(connection)
        if not attempted:
            continue
        if suffix.isascii() and suffix.isdecimal():
            canonical_index = suffix.lstrip("0") or "0"
            if canonical_index == str(index):
                return True
        info = connection.get("info") if isinstance(connection, dict) else None
        raw_id = info.get("id") if isinstance(info, dict) else None
        if isinstance(raw_id, str) and suffix == raw_id:
            return True
        if server_id is not None and suffix == server_id:
            return True
    return False


def get_async_tool_function_and_apply_extra_params(
    function: Callable, extra_params: dict
) -> Callable[..., Awaitable]:
    sig = inspect.signature(function)
    extra_params = {k: v for k, v in extra_params.items() if k in sig.parameters}
    partial_func = partial(function, **extra_params)

    if inspect.iscoroutinefunction(function):
        update_wrapper(partial_func, function)
        return partial_func
    else:
        # Make it a coroutine function
        async def new_function(*args, **kwargs):
            return partial_func(*args, **kwargs)

        update_wrapper(new_function, function)
        return new_function


def get_tools(
    request: Request, tool_ids: list[str], user: UserModel, extra_params: dict
) -> dict[str, dict]:
    tools_dict = {}

    for tool_id in tool_ids:
        is_server_reference = isinstance(tool_id, str) and tool_id.startswith("server:")
        connections = (
            request.app.state.config.TOOL_SERVER_CONNECTIONS
            if is_server_reference
            else None
        )
        reserved_named = (
            is_reserved_named_server_reference(tool_id, connections)
            if is_server_reference
            else False
        )
        resolved_named_server = None
        # Named server IDs are a separate namespace. Never let a local plugin row
        # with the same textual ID shadow the server or bypass its role gate.
        if reserved_named:
            resolved_named_server = resolve_tool_server_reference(
                tool_id, connections, request.app.state.TOOL_SERVERS, user
            )
            if resolved_named_server is None:
                continue
        if reserved_named and Tools.get_tool_by_id(tool_id) is not None:
            # Reject ambiguous local/server IDs before constructing a callable.
            continue
        tool = None if reserved_named else Tools.get_tool_by_id(tool_id)
        if tool is None:
            if is_server_reference:
                resolved_server = resolved_named_server or resolve_tool_server_reference(
                    tool_id, connections, request.app.state.TOOL_SERVERS, user
                )
                if resolved_server is None:
                    continue
                tool_server_connection, tool_server_data = resolved_server
                specs = tool_server_data.get("specs", [])

                for spec in specs:
                    function_name = spec["name"]

                    auth_type = tool_server_connection.get("auth_type", "bearer")
                    token = None

                    if auth_type == "bearer":
                        token = tool_server_connection.get("key", "")
                    elif auth_type == "session":
                        token = request.state.token.credentials

                    def make_tool_function(function_name, token, tool_server_data):
                        async def tool_function(**kwargs):
                            print(
                                f"Executing tool function {function_name} with params: {kwargs}"
                            )
                            return await execute_tool_server(
                                token=token,
                                url=tool_server_data["url"],
                                name=function_name,
                                params=kwargs,
                                server_data=tool_server_data,
                            )

                        return tool_function

                    tool_function = make_tool_function(
                        function_name, token, tool_server_data
                    )

                    callable = get_async_tool_function_and_apply_extra_params(
                        tool_function,
                        {},
                    )

                    tool_dict = {
                        "tool_id": tool_id,
                        "callable": callable,
                        "spec": spec,
                    }

                    # TODO: if collision, prepend toolkit name
                    if function_name in tools_dict:
                        log.warning(
                            f"Tool {function_name} already exists in another tools!"
                        )
                        log.warning(f"Discarding {tool_id}.{function_name}")
                    else:
                        tools_dict[function_name] = tool_dict
            else:
                continue
        else:
            module = request.app.state.TOOLS.get(tool_id, None)
            if module is None:
                module, _ = load_tool_module_by_id(tool_id)
                request.app.state.TOOLS[tool_id] = module

            extra_params["__id__"] = tool_id

            # Set valves for the tool
            if hasattr(module, "valves") and hasattr(module, "Valves"):
                valves = Tools.get_tool_valves_by_id(tool_id) or {}
                module.valves = module.Valves(**valves)
            if hasattr(module, "UserValves"):
                extra_params["__user__"]["valves"] = module.UserValves(  # type: ignore
                    **Tools.get_user_valves_by_id_and_user_id(tool_id, user.id)
                )

            for spec in tool.specs:
                # TODO: Fix hack for OpenAI API
                # Some times breaks OpenAI but others don't. Leaving the comment
                for val in spec.get("parameters", {}).get("properties", {}).values():
                    if val["type"] == "str":
                        val["type"] = "string"

                # Remove internal reserved parameters (e.g. __id__, __user__)
                spec["parameters"]["properties"] = {
                    key: val
                    for key, val in spec["parameters"]["properties"].items()
                    if not key.startswith("__")
                }

                # convert to function that takes only model params and inserts custom params
                function_name = spec["name"]
                tool_function = getattr(module, function_name)
                callable = get_async_tool_function_and_apply_extra_params(
                    tool_function, extra_params
                )

                # TODO: Support Pydantic models as parameters
                if callable.__doc__ and callable.__doc__.strip() != "":
                    s = re.split(":(param|return)", callable.__doc__, 1)
                    spec["description"] = s[0]
                else:
                    spec["description"] = function_name

                tool_dict = {
                    "tool_id": tool_id,
                    "callable": callable,
                    "spec": spec,
                    # Misc info
                    "metadata": {
                        "file_handler": hasattr(module, "file_handler")
                        and module.file_handler,
                        "citation": hasattr(module, "citation") and module.citation,
                    },
                }

                # TODO: if collision, prepend toolkit name
                if function_name in tools_dict:
                    log.warning(
                        f"Tool {function_name} already exists in another tools!"
                    )
                    log.warning(f"Discarding {tool_id}.{function_name}")
                else:
                    tools_dict[function_name] = tool_dict

    return tools_dict


def parse_description(docstring: str | None) -> str:
    """
    Parse a function's docstring to extract the description.

    Args:
        docstring (str): The docstring to parse.

    Returns:
        str: The description.
    """

    if not docstring:
        return ""

    lines = [line.strip() for line in docstring.strip().split("\n")]
    description_lines: list[str] = []

    for line in lines:
        if re.match(r":param", line) or re.match(r":return", line):
            break

        description_lines.append(line)

    return "\n".join(description_lines)


def parse_docstring(docstring):
    """
    Parse a function's docstring to extract parameter descriptions in reST format.

    Args:
        docstring (str): The docstring to parse.

    Returns:
        dict: A dictionary where keys are parameter names and values are descriptions.
    """
    if not docstring:
        return {}

    # Regex to match `:param name: description` format
    param_pattern = re.compile(r":param (\w+):\s*(.+)")
    param_descriptions = {}

    for line in docstring.splitlines():
        match = param_pattern.match(line.strip())
        if not match:
            continue
        param_name, param_description = match.groups()
        if param_name.startswith("__"):
            continue
        param_descriptions[param_name] = param_description

    return param_descriptions


def convert_function_to_pydantic_model(func: Callable) -> type[BaseModel]:
    """
    Converts a Python function's type hints and docstring to a Pydantic model,
    including support for nested types, default values, and descriptions.

    Args:
        func: The function whose type hints and docstring should be converted.
        model_name: The name of the generated Pydantic model.

    Returns:
        A Pydantic model class.
    """
    type_hints = get_type_hints(func)
    signature = inspect.signature(func)
    parameters = signature.parameters

    docstring = func.__doc__

    description = parse_description(docstring)
    function_descriptions = parse_docstring(docstring)

    field_defs = {}
    for name, param in parameters.items():

        type_hint = type_hints.get(name, Any)
        default_value = param.default if param.default is not param.empty else ...

        description = function_descriptions.get(name, None)

        if description:
            field_defs[name] = type_hint, Field(default_value, description=description)
        else:
            field_defs[name] = type_hint, default_value

    model = create_model(func.__name__, **field_defs)
    model.__doc__ = description

    return model


def get_functions_from_tool(tool: object) -> list[Callable]:
    return [
        getattr(tool, func)
        for func in dir(tool)
        if callable(
            getattr(tool, func)
        )  # checks if the attribute is callable (a method or function).
        and not func.startswith(
            "__"
        )  # filters out special (dunder) methods like init, str, etc. — these are usually built-in functions of an object that you might not need to use directly.
        and not inspect.isclass(
            getattr(tool, func)
        )  # ensures that the callable is not a class itself, just a method or function.
    ]


def get_tool_specs(tool_module: object) -> list[dict]:
    function_models = map(
        convert_function_to_pydantic_model, get_functions_from_tool(tool_module)
    )

    specs = [
        convert_pydantic_model_to_openai_function_spec(function_model)
        for function_model in function_models
    ]

    return specs


def resolve_schema(schema, components):
    """
    Recursively resolves a JSON schema using OpenAPI components.
    """
    if not schema:
        return {}

    if "$ref" in schema:
        ref_path = schema["$ref"]
        ref_parts = ref_path.strip("#/").split("/")
        resolved = components
        for part in ref_parts[1:]:  # Skip the initial 'components'
            resolved = resolved.get(part, {})
        return resolve_schema(resolved, components)

    resolved_schema = copy.deepcopy(schema)

    # Recursively resolve inner schemas
    if "properties" in resolved_schema:
        for prop, prop_schema in resolved_schema["properties"].items():
            resolved_schema["properties"][prop] = resolve_schema(
                prop_schema, components
            )

    if "items" in resolved_schema:
        resolved_schema["items"] = resolve_schema(resolved_schema["items"], components)

    return resolved_schema


def convert_openapi_to_tool_payload(openapi_spec):
    """
    Converts an OpenAPI specification into a custom tool payload structure.

    Args:
        openapi_spec (dict): The OpenAPI specification as a Python dict.

    Returns:
        list: A list of tool payloads.
    """
    tool_payload = []

    for path, methods in openapi_spec.get("paths", {}).items():
        for method, operation in methods.items():
            tool = {
                "type": "function",
                "name": operation.get("operationId"),
                "description": operation.get(
                    "description", operation.get("summary", "No description available.")
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            }

            # Extract path and query parameters
            for param in operation.get("parameters", []):
                param_name = param["name"]
                param_schema = param.get("schema", {})
                tool["parameters"]["properties"][param_name] = {
                    "type": param_schema.get("type"),
                    "description": param_schema.get("description", ""),
                }
                if param.get("required"):
                    tool["parameters"]["required"].append(param_name)

            # Extract and resolve requestBody if available
            request_body = operation.get("requestBody")
            if request_body:
                content = request_body.get("content", {})
                json_schema = content.get("application/json", {}).get("schema")
                if json_schema:
                    resolved_schema = resolve_schema(
                        json_schema, openapi_spec.get("components", {})
                    )

                    if resolved_schema.get("properties"):
                        tool["parameters"]["properties"].update(
                            resolved_schema["properties"]
                        )
                        if "required" in resolved_schema:
                            tool["parameters"]["required"] = list(
                                set(
                                    tool["parameters"]["required"]
                                    + resolved_schema["required"]
                                )
                            )
                    elif resolved_schema.get("type") == "array":
                        tool["parameters"] = resolved_schema  # special case for array

            tool_payload.append(tool)

    return tool_payload


async def get_tool_server_data(token: str, url: str) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    error = None
    try:
        timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_body = await response.json()
                    raise Exception(error_body)

                # Check if URL ends with .yaml or .yml to determine format
                if url.lower().endswith((".yaml", ".yml")):
                    text_content = await response.text()
                    res = yaml.safe_load(text_content)
                else:
                    res = await response.json()
    except Exception as err:
        log.exception(f"Could not fetch tool server spec from {url}")
        if isinstance(err, dict) and "detail" in err:
            error = err["detail"]
        else:
            error = str(err)
        raise Exception(error)

    data = {
        "openapi": res,
        "info": res.get("info", {}),
        "specs": convert_openapi_to_tool_payload(res),
    }

    print("Fetched data:", data)
    return data


async def get_tool_servers_data(
    servers: List[Dict[str, Any]], session_token: Optional[str] = None
) -> List[Dict[str, Any]]:
    # Prepare list of enabled servers along with their original index
    server_entries = []
    for idx, server in enumerate(servers):
        if server.get("config", {}).get("enable"):
            url_path = server.get("path", "openapi.json")
            full_url = f"{server.get('url')}/{url_path}"

            auth_type = server.get("auth_type", "bearer")
            token = None

            if auth_type == "bearer":
                token = server.get("key", "")
            elif auth_type == "session":
                token = session_token
            server_entries.append((idx, server, full_url, token))

    # Create async tasks to fetch data
    tasks = [get_tool_server_data(token, url) for (_, _, url, token) in server_entries]

    # Execute tasks concurrently
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Build final results with index and server metadata
    results = []
    for (idx, server, url, _), response in zip(server_entries, responses):
        if isinstance(response, Exception):
            print(f"Failed to connect to {url} OpenAPI tool server")
            continue

        results.append(
            {
                "idx": idx,
                "url": server.get("url"),
                "connection_binding": _tool_server_connection_binding(server),
                "openapi": response.get("openapi"),
                "info": response.get("info"),
                "specs": response.get("specs"),
            }
        )

    return results


async def execute_tool_server(
    token: str, url: str, name: str, params: Dict[str, Any], server_data: Dict[str, Any]
) -> Any:
    error = None
    try:
        openapi = server_data.get("openapi", {})
        paths = openapi.get("paths", {})

        matching_route = None
        for route_path, methods in paths.items():
            for http_method, operation in methods.items():
                if isinstance(operation, dict) and operation.get("operationId") == name:
                    matching_route = (route_path, methods)
                    break
            if matching_route:
                break

        if not matching_route:
            raise Exception(f"No matching route found for operationId: {name}")

        route_path, methods = matching_route

        method_entry = None
        for http_method, operation in methods.items():
            if operation.get("operationId") == name:
                method_entry = (http_method.lower(), operation)
                break

        if not method_entry:
            raise Exception(f"No matching method found for operationId: {name}")

        http_method, operation = method_entry

        path_params = {}
        query_params = {}
        body_params = {}

        for param in operation.get("parameters", []):
            param_name = param["name"]
            param_in = param["in"]
            if param_name in params:
                if param_in == "path":
                    path_params[param_name] = params[param_name]
                elif param_in == "query":
                    query_params[param_name] = params[param_name]

        final_url = f"{url}{route_path}"
        for key, value in path_params.items():
            final_url = final_url.replace(f"{{{key}}}", str(value))

        if query_params:
            query_string = "&".join(f"{k}={v}" for k, v in query_params.items())
            final_url = f"{final_url}?{query_string}"

        if operation.get("requestBody", {}).get("content"):
            if params:
                body_params = params
            else:
                raise Exception(
                    f"Request body expected for operation '{name}' but none found."
                )

        headers = {"Content-Type": "application/json"}

        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with aiohttp.ClientSession() as session:
            request_method = getattr(session, http_method.lower())

            if http_method in ["post", "put", "patch"]:
                async with request_method(
                    final_url, json=body_params, headers=headers
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise Exception(f"HTTP error {response.status}: {text}")
                    return await response.json()
            else:
                async with request_method(final_url, headers=headers) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise Exception(f"HTTP error {response.status}: {text}")
                    return await response.json()

    except Exception as err:
        error = str(err)
        print("API Request Error:", error)
        return {"error": error}
