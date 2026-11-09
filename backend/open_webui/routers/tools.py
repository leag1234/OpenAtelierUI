import asyncio
import logging
from pathlib import Path
from typing import Annotated, Any, Optional
import time
import unicodedata
from urllib.parse import urlsplit

from open_webui.models.tools import (
    ToolForm,
    ToolModel,
    ToolResponse,
    ToolUserResponse,
    Tools,
)
from open_webui.models.users import Users
from open_webui.utils.imported_history import (
    HistoryNotFound,
    HistoryUnavailable,
    get_history as get_imported_tool_history,
    get_current_version_id as get_imported_tool_current_version_id,
    list_history as list_imported_tool_history,
)
from open_webui.utils.version_history import (
    VersionHistoryNotFound,
    VersionHistoryUnavailable,
    append_version,
    compare_versions,
    current_version_id,
    delete_version,
    ensure_root,
    get_version,
    list_versions,
    select_version,
)
from open_webui.utils.plugin import load_tool_module_by_id, replace_imports
from open_webui.utils.bounded_url_loader import LoadUrlForm, load_source_url
from open_webui.config import CACHE_DIR
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, WithJsonSchema
from open_webui.utils.tools import (
    _named_server_config,
    _tool_server_connection_binding,
    get_tool_specs,
    named_tool_server_catalog_ids,
    named_tool_server_read_allowed,
    resolve_tool_server_reference,
)
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_access, has_permission
from open_webui.utils.access_grants import (
    access_control_for_grants,
    access_grants_for_acl,
    preserve_access_control_for_update,
)
from open_webui.env import DATA_DIR, DATABASE_URL, SRC_LOG_LEVELS

from open_webui.utils.tools import get_tool_servers_data

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


router = APIRouter()
JsonObject = Annotated[
    dict[str, Any], WithJsonSchema({"type": "object", "additionalProperties": True})
]


class ToolVersionForm(BaseModel):
    version_id: str


def _tool_snapshot(tool) -> dict:
    return {
        "name": tool.name,
        "content": tool.content,
        "meta": tool.meta.model_dump(exclude={"manifest"}),
    }


def _tool_current_version(tool) -> Optional[str]:
    try:
        return current_version_id(DATA_DIR, "tool", tool.id, tool.user_id)
    except VersionHistoryNotFound:
        try:
            return get_imported_tool_current_version_id(
                DATA_DIR, DATABASE_URL, "tool", tool
            )
        except (HistoryNotFound, HistoryUnavailable):
            return None


def _record_tool(tool, user_id: str, commit_message: Optional[str]) -> str:
    return append_version(
        DATA_DIR,
        "tool",
        tool.id,
        tool.user_id,
        _tool_snapshot(tool),
        user_id,
        commit_message,
    )


def _require_tool_owner(tool, user) -> None:
    if user.role != "admin" and tool.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


@router.post("/load/url", response_model=Optional[JsonObject])
async def load_tool_from_url(form_data: LoadUrlForm, user=Depends(get_admin_user)):
    try:
        content, name = await load_source_url(str(form_data.url))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return {"content": content, "name": name}


def _valid_named_server_display_text(value, maximum, *, nonempty):
    return (
        isinstance(value, str)
        and len(value) <= maximum
        and (not nonempty or bool(value.strip()))
        and not any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    )


async def _refresh_named_tool_server_catalog(connections, servers):
    """Refresh only unique enabled named servers using the measured no-auth shape."""
    if not isinstance(connections, list) or not isinstance(servers, list):
        return servers

    ids = {}
    id_counts = {}
    for index, connection in enumerate(connections):
        attempted, server_id = _named_server_config(connection)
        info = connection.get("info") if isinstance(connection, dict) else None
        raw_id = info.get("id") if attempted and isinstance(info, dict) else None
        if isinstance(raw_id, str):
            id_counts[raw_id] = id_counts.get(raw_id, 0) + 1
        if attempted and server_id is not None:
            ids[index] = server_id

    eligible = {}
    for index, server_id in ids.items():
        connection = connections[index]
        config = connection.get("config")
        url = connection.get("url")
        try:
            url_parts = urlsplit(url) if isinstance(url, str) else None
            valid_url = (
                url_parts is not None
                and url_parts.scheme in {"http", "https"}
                and bool(url_parts.hostname)
                and url_parts.username is None
                and url_parts.password is None
                and not url_parts.fragment
            )
        except ValueError:
            valid_url = False
        if (
            id_counts[server_id] == 1
            and isinstance(config, dict)
            and config.get("enable") is True
            and valid_url
            and connection.get("auth_type") == "none"
            and connection.get("key", "") == ""
            and connection.get("headers") in (None, {})
            and connection.get("forward_cookies", False) is False
        ):
            eligible[index] = connection
    if not eligible:
        return servers

    indices = list(eligible)
    fetched = await asyncio.gather(
        *(get_tool_servers_data([eligible[index]]) for index in indices),
        return_exceptions=True,
    )
    fresh_by_index = {}
    for index, connection, result in zip(indices, (eligible[i] for i in indices), fetched):
        if isinstance(result, Exception) or not isinstance(result, list) or len(result) != 1:
            continue
        entry = result[0]
        if (
            not isinstance(entry, dict)
            or type(entry.get("idx")) is not int
            or entry.get("idx") != 0
            or entry.get("connection_binding") != _tool_server_connection_binding(connection)
        ):
            continue
        fresh_by_index[index] = {**entry, "idx": index}

    refreshed = []
    emitted = set()
    for entry in servers:
        index = entry.get("idx") if isinstance(entry, dict) else None
        if isinstance(index, bool) or not isinstance(index, int):
            refreshed.append(entry)
            continue
        if index not in eligible:
            refreshed.append(entry)
        elif index not in emitted:
            emitted.add(index)
            if index in fresh_by_index:
                refreshed.append(fresh_by_index[index])
    for index in indices:
        if index not in emitted and index in fresh_by_index:
            refreshed.append(fresh_by_index[index])
    return refreshed


def _tool_access_fields(tool, user):
    owner_or_admin = tool.user_id == user.id or user.role == "admin"
    return {
        "access_grants": access_grants_for_acl(
            tool.access_control, "tool", tool.id, tool.created_at
        ),
        "write_access": owner_or_admin
        or has_access(user.id, "write", tool.access_control),
    }

############################
# GetTools
############################


@router.get("/")
async def get_tools(request: Request, user=Depends(get_verified_user)):

    if not request.app.state.TOOL_SERVERS:
        # If the tool servers are not set, we need to set them
        # This is done only once when the server starts
        # This is done to avoid loading the tool servers every time

        request.app.state.TOOL_SERVERS = await get_tool_servers_data(
            request.app.state.config.TOOL_SERVER_CONNECTIONS
        )
    else:
        request.app.state.TOOL_SERVERS = await _refresh_named_tool_server_catalog(
            request.app.state.config.TOOL_SERVER_CONNECTIONS,
            request.app.state.TOOL_SERVERS,
        )

    tools = Tools.get_tools()
    connections = request.app.state.config.TOOL_SERVER_CONNECTIONS
    indexed_servers = [
        server
        for server in request.app.state.TOOL_SERVERS
        if isinstance(server, dict)
        and isinstance(server.get("idx"), int)
        and not isinstance(server.get("idx"), bool)
    ]
    named_catalog_ids, named_indices = named_tool_server_catalog_ids(
        connections, indexed_servers
    )
    named_server_object_ids = set()
    for server in request.app.state.TOOL_SERVERS:
        server_idx = server.get("idx")
        if not isinstance(server_idx, int) or isinstance(server_idx, bool):
            continue
        if server_idx < 0 or server_idx >= len(connections):
            continue
        connection = connections[server_idx]
        if not isinstance(connection, dict):
            continue
        if server_idx in named_indices:
            catalog_id = named_catalog_ids.get(server_idx)
            if catalog_id is None or not named_tool_server_read_allowed(
                connection, user
            ):
                continue
        else:
            catalog_id = f"server:{server_idx}"
        connection_config = connection.get("config")
        if not isinstance(connection_config, dict):
            connection_config = {}
        schema_info = server["openapi"].get("info", {})
        display_name = schema_info.get("title", "Tool Server")
        display_description = schema_info.get("description", "")
        if server_idx in named_indices:
            configured_info = connection.get("info")
            if isinstance(configured_info, dict):
                configured_name = configured_info.get("name")
                configured_description = configured_info.get("description")
                if _valid_named_server_display_text(
                    configured_name, 256, nonempty=True
                ):
                    display_name = configured_name
                if _valid_named_server_display_text(
                    configured_description, 1024, nonempty=False
                ):
                    display_description = configured_description
        tool_response = ToolUserResponse(
            **{
                "id": catalog_id,
                "user_id": catalog_id,
                "name": display_name,
                "meta": {
                    "description": display_description,
                },
                "access_control": connection_config.get("access_control", None),
                "updated_at": int(time.time()),
                "created_at": int(time.time()),
            }
        )
        tools.append(tool_response)
        if server_idx in named_indices:
            named_server_object_ids.add(id(tool_response))

    if user.role != "admin":
        tools = [
            tool
            for tool in tools
            if id(tool) in named_server_object_ids
            or tool.user_id == user.id
            or has_access(user.id, "read", tool.access_control)
        ]

    responses = []
    for tool in tools:
        response = tool.model_dump(exclude={"content"})
        response["content"] = None
        response["specs"] = getattr(tool, "specs", [])
        response["user"] = None
        response.update(_tool_access_fields(tool, user))
        if id(tool) in named_server_object_ids:
            # Match only the measured named-server metadata keys and empty grant
            # projection. Do not expose the configured connection permission.
            response = {
                "access_grants": [],
                "created_at": response.get("created_at"),
                "id": response.get("id"),
                "meta": response.get("meta"),
                "name": response.get("name"),
                "updated_at": response.get("updated_at"),
                "user": None,
                "user_id": response.get("user_id"),
                "version_id": None,
            }
        responses.append(response)
    return responses


############################
# GetToolList
############################


@router.get("/list")
async def get_tool_list(user=Depends(get_verified_user)):
    if user.role == "admin":
        tools = Tools.get_tools()
    else:
        tools = Tools.get_tools_by_user_id(user.id, "read")
    responses = []
    for tool in tools:
        response = tool.model_dump(exclude={"content"})
        response["content"] = None
        response["specs"] = getattr(tool, "specs", [])
        response["user"] = None
        response.update(_tool_access_fields(tool, user))
        responses.append(response)
    return responses


############################
# ExportTools
############################


@router.get("/export", response_model=list[ToolModel])
async def export_tools(user=Depends(get_admin_user)):
    tools = Tools.get_tools()
    return tools


############################
# CreateNewTools
############################


@router.post("/create")
async def create_new_tools(
    request: Request,
    form_data: ToolForm,
    user=Depends(get_verified_user),
):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.tools", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    if not form_data.id.isidentifier():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only alphanumeric characters and underscores are allowed in the id",
        )

    form_data.id = form_data.id.lower()

    tools = Tools.get_tool_by_id(form_data.id)
    if tools is None:
        try:
            form_data.content = replace_imports(form_data.content)
            tool_module, frontmatter = load_tool_module_by_id(
                form_data.id, content=form_data.content
            )
            form_data.meta.manifest = frontmatter

            TOOLS = request.app.state.TOOLS
            TOOLS[form_data.id] = tool_module

            specs = get_tool_specs(TOOLS[form_data.id])
            tools = Tools.insert_new_tool(user.id, form_data, specs)

            tool_cache_dir = CACHE_DIR / "tools" / form_data.id
            tool_cache_dir.mkdir(parents=True, exist_ok=True)

            if tools:
                tools.version_id = _record_tool(
                    tools, user.id, form_data.commit_message
                )
                response = tools.model_dump(exclude={"content", "specs"})
                response.update(_tool_access_fields(tools, user))
                response["user"] = None
                return response
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error creating tools"),
                )
        except Exception as e:
            log.exception(f"Failed to load the tool by id {form_data.id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(str(e)),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )


############################
# GetToolsById
############################


@router.get("/id/{id}")
async def get_tools_by_id(id: str, user=Depends(get_verified_user)):
    tools = Tools.get_tool_by_id(id)

    if tools:
        if (
            user.role == "admin"
            or tools.user_id == user.id
            or has_access(user.id, "read", tools.access_control)
        ):
            response = tools.model_dump()
            response.update(_tool_access_fields(tools, user))
            response["user"] = None
            if tools.user_id != user.id and user.role != "admin":
                response.pop("content", None)
            response["version_id"] = _tool_current_version(tools)
            return response
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get(
    "/id/{id}/specs",
    operation_id="get_tool_specs_api_v1_tools_id__id__specs_get",
    summary="Get Tool Specs",
)
async def get_tool_specs_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    """Discover tools for an accessible connection. Currently supports MCP."""
    connections = request.app.state.config.TOOL_SERVER_CONNECTIONS
    if not request.app.state.TOOL_SERVERS:
        request.app.state.TOOL_SERVERS = await get_tool_servers_data(connections)
    else:
        request.app.state.TOOL_SERVERS = await _refresh_named_tool_server_catalog(
            connections,
            request.app.state.TOOL_SERVERS,
        )

    resolved = resolve_tool_server_reference(
        id, connections, request.app.state.TOOL_SERVERS, user
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    _connection, server = resolved
    return {
        "specs": [
            {
                "name": spec.get("name"),
                "description": spec.get("description", ""),
            }
            for spec in server.get("specs", [])
            if isinstance(spec, dict) and isinstance(spec.get("name"), str)
        ]
    }


@router.post("/id/{id}/toggle", response_model=ToolResponse)
async def toggle_tool_by_id(id: str, user=Depends(get_verified_user)):
    tool = Tools.get_tool_by_id(id)
    if tool is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    if user.role != "admin" and tool.user_id != user.id:
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.UNAUTHORIZED)

    toggled = Tools.toggle_tool_by_id(id)
    if toggled is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    toggled.version_id = _tool_current_version(toggled)
    response = toggled.model_dump(exclude={"content", "specs"})
    response["access_grants"] = access_grants_for_acl(
        toggled.access_control, "tool", toggled.id, toggled.created_at
    )
    return response


@router.get("/id/{id}/history")
async def get_tool_history(id: str, page: int = 1, user=Depends(get_verified_user)):
    tool = Tools.get_tool_by_id(id)
    if not tool:
        raise HTTPException(status_code=404, detail="Not found")
    _require_tool_owner(tool, user)
    try:
        return list_versions(
            DATA_DIR, "tool", tool.id, tool.user_id, page, Users.get_user_by_id
        )
    except VersionHistoryNotFound:
        pass
    except VersionHistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    try:
        return list_imported_tool_history(DATA_DIR, DATABASE_URL, "tool", tool, page, Users.get_user_by_id)
    except HistoryNotFound:
        raise HTTPException(status_code=404, detail="Not found")
    except HistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))


async def get_tool_history_version(id: str, history_id: str, user=Depends(get_verified_user)):
    tool = Tools.get_tool_by_id(id)
    if not tool:
        raise HTTPException(status_code=404, detail="Not found")
    _require_tool_owner(tool, user)
    try:
        return get_version(
            DATA_DIR,
            "tool",
            tool.id,
            tool.user_id,
            history_id,
            Users.get_user_by_id,
        )
    except VersionHistoryNotFound:
        pass
    except VersionHistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    try:
        return get_imported_tool_history(DATA_DIR, DATABASE_URL, "tool", tool, history_id, Users.get_user_by_id)
    except HistoryNotFound:
        raise HTTPException(status_code=404, detail="Version not found")
    except HistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))


@router.get(
    "/id/{id}/history/diff",
    operation_id="get_tool_history_diff_api_v1_tools_id__id__history_diff_get",
)
async def get_tool_history_diff(
    id: str, from_id: str, to_id: str, user=Depends(get_verified_user)
):
    tool = Tools.get_tool_by_id(id)
    if not tool:
        raise HTTPException(status_code=404, detail="Not found")
    _require_tool_owner(tool, user)
    try:
        return compare_versions(
            DATA_DIR,
            "tool",
            tool.id,
            tool.user_id,
            from_id,
            to_id,
            Users.get_user_by_id,
        )
    except VersionHistoryNotFound:
        raise HTTPException(status_code=404, detail="Version not found")
    except VersionHistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))


@router.delete(
    "/id/{id}/history/{history_id}",
    operation_id="delete_tool_history_entry_api_v1_tools_id__id__history__history_id__delete",
)
async def delete_tool_history_entry(
    id: str, history_id: str, user=Depends(get_verified_user)
):
    tool = Tools.get_tool_by_id(id)
    if not tool:
        raise HTTPException(status_code=404, detail="Not found")
    _require_tool_owner(tool, user)
    try:
        return delete_version(DATA_DIR, "tool", tool.id, tool.user_id, history_id)
    except VersionHistoryNotFound:
        raise HTTPException(status_code=404, detail="Version not found")
    except VersionHistoryUnavailable as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post(
    "/id/{id}/update/version",
    response_model=ToolModel,
    operation_id="set_tool_production_api_v1_tools_id__id__update_version_post",
)
async def set_tool_production(
    request: Request,
    id: str,
    form_data: ToolVersionForm,
    user=Depends(get_verified_user),
):
    tool = Tools.get_tool_by_id(id)
    if not tool:
        raise HTTPException(status_code=404, detail="Not found")
    _require_tool_owner(tool, user)
    try:
        entry = get_version(
            DATA_DIR,
            "tool",
            tool.id,
            tool.user_id,
            form_data.version_id,
            Users.get_user_by_id,
        )
        selected = ToolForm(id=id, **entry["snapshot"])
        selected.content = replace_imports(selected.content)
        module, frontmatter = load_tool_module_by_id(id, content=selected.content)
        selected.meta.manifest = frontmatter
        request.app.state.TOOLS[id] = module
        specs = get_tool_specs(module)
        updated = Tools.update_tool_by_id(
            id,
            {
                **selected.model_dump(exclude={"id", "commit_message", "access_control"}),
                "access_control": tool.access_control,
                "specs": specs,
            },
        )
        if not updated:
            raise VersionHistoryUnavailable("Error selecting tool version")
        select_version(DATA_DIR, "tool", tool.id, tool.user_id, form_data.version_id)
        response = updated.model_dump(mode="json", exclude={"access_control"})
        response["access_grants"] = access_grants_for_acl(
            updated.access_control, "tool", updated.id, updated.created_at
        )
        response.setdefault("meta", {})["has_user_valves"] = False
        response["meta"].setdefault("i18n", None)
        response["version_id"] = form_data.version_id
        return JSONResponse(content=response)
    except VersionHistoryNotFound:
        raise HTTPException(status_code=404, detail="Version not found")
    except (VersionHistoryUnavailable, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error))


router.add_api_route(
    "/id/{id}/history/{history_id}",
    get_tool_history_version,
    methods=["GET"],
    operation_id="get_tool_history_entry_api_v1_tools_id__id__history__history_id__get",
    summary="Get Tool History Entry",
)


@router.post("/id/{id}/access/update")
async def update_tools_access_by_id(
    id: str, form_data: dict, user=Depends(get_verified_user)
):
    tool = Tools.get_tool_by_id(id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    if tool.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )
    try:
        access_control = access_control_for_grants(form_data.get("access_grants"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    tool = Tools.update_tool_by_id(id, {"access_control": access_control})
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error updating tool access"),
        )
    response = tool.model_dump()
    response.update(_tool_access_fields(tool, user))
    response["user"] = None
    return response


############################
# UpdateToolsById
############################


@router.post("/id/{id}/update")
async def update_tools_by_id(
    request: Request,
    id: str,
    form_data: ToolForm,
    user=Depends(get_verified_user),
):
    tools = Tools.get_tool_by_id(id)
    if not tools:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # Is the user the original creator, in a group with write access, or an admin
    if (
        tools.user_id != user.id
        and not has_access(user.id, "write", tools.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    if user.role != "admin" and not has_permission(
        user.id, "workspace.tools", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    try:
        ensure_root(
            DATA_DIR,
            "tool",
            tools.id,
            tools.user_id,
            _tool_snapshot(tools),
            user.id,
        )
        form_data.content = replace_imports(form_data.content)
        tool_module, frontmatter = load_tool_module_by_id(id, content=form_data.content)
        form_data.meta.manifest = frontmatter

        TOOLS = request.app.state.TOOLS
        TOOLS[id] = tool_module

        specs = get_tool_specs(TOOLS[id])

        updated = {
            **form_data.model_dump(exclude={"id", "commit_message"}),
            "specs": specs,
        }
        updated = preserve_access_control_for_update(updated, tools.access_control)

        log.debug(updated)
        tools = Tools.update_tool_by_id(id, updated)

        if tools:
            tools.version_id = _record_tool(
                tools, user.id, form_data.commit_message
            )
            response = tools.model_dump()
            response.update(_tool_access_fields(tools, user))
            response["user"] = None
            return response
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating tools"),
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )


############################
# DeleteToolsById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_tools_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)
    if not tools:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        tools.user_id != user.id
        and not has_access(user.id, "write", tools.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    result = Tools.delete_tool_by_id(id)
    if result:
        TOOLS = request.app.state.TOOLS
        if id in TOOLS:
            del TOOLS[id]

    return result


############################
# GetToolValves
############################


@router.get("/id/{id}/valves", response_model=Optional[dict])
async def get_tools_valves_by_id(id: str, user=Depends(get_verified_user)):
    tools = Tools.get_tool_by_id(id)
    if tools:
        if user.role != "admin" and tools.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        try:
            valves = Tools.get_tool_valves_by_id(id)
            return valves
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(str(e)),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# GetToolValvesSpec
############################


@router.get("/id/{id}/valves/spec", response_model=Optional[dict])
async def get_tools_valves_spec_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)
    if tools:
        if user.role != "admin" and tools.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        if id in request.app.state.TOOLS:
            tools_module = request.app.state.TOOLS[id]
        else:
            tools_module, _ = load_tool_module_by_id(id)
            request.app.state.TOOLS[id] = tools_module

        if hasattr(tools_module, "Valves"):
            Valves = tools_module.Valves
            return Valves.schema()
        return None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateToolValves
############################


@router.post("/id/{id}/valves/update", response_model=Optional[dict])
async def update_tools_valves_by_id(
    request: Request, id: str, form_data: dict, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)
    if not tools:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        tools.user_id != user.id
        and not has_access(user.id, "write", tools.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if id in request.app.state.TOOLS:
        tools_module = request.app.state.TOOLS[id]
    else:
        tools_module, _ = load_tool_module_by_id(id)
        request.app.state.TOOLS[id] = tools_module

    if not hasattr(tools_module, "Valves"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    Valves = tools_module.Valves

    try:
        form_data = {k: v for k, v in form_data.items() if v is not None}
        valves = Valves(**form_data)
        Tools.update_tool_valves_by_id(id, valves.model_dump())
        return valves.model_dump()
    except Exception as e:
        log.exception(f"Failed to update tool valves by id {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )


############################
# ToolUserValves
############################


@router.get("/id/{id}/valves/user", response_model=Optional[dict])
async def get_tools_user_valves_by_id(id: str, user=Depends(get_verified_user)):
    tools = Tools.get_tool_by_id(id)
    if tools:
        if (
            user.role != "admin"
            and tools.user_id != user.id
            and not has_access(user.id, "read", tools.access_control)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        try:
            user_valves = Tools.get_user_valves_by_id_and_user_id(id, user.id)
            return user_valves
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(str(e)),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get("/id/{id}/valves/user/spec", response_model=Optional[dict])
async def get_tools_user_valves_spec_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)
    if tools:
        if (
            user.role != "admin"
            and tools.user_id != user.id
            and not has_access(user.id, "read", tools.access_control)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        if id in request.app.state.TOOLS:
            tools_module = request.app.state.TOOLS[id]
        else:
            tools_module, _ = load_tool_module_by_id(id)
            request.app.state.TOOLS[id] = tools_module

        if hasattr(tools_module, "UserValves"):
            UserValves = tools_module.UserValves
            return UserValves.schema()
        return None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.post("/id/{id}/valves/user/update", response_model=Optional[dict])
async def update_tools_user_valves_by_id(
    request: Request, id: str, form_data: dict, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)

    if tools:
        if (
            user.role != "admin"
            and tools.user_id != user.id
            and not has_access(user.id, "read", tools.access_control)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        if id in request.app.state.TOOLS:
            tools_module = request.app.state.TOOLS[id]
        else:
            tools_module, _ = load_tool_module_by_id(id)
            request.app.state.TOOLS[id] = tools_module

        if hasattr(tools_module, "UserValves"):
            UserValves = tools_module.UserValves

            try:
                form_data = {k: v for k, v in form_data.items() if v is not None}
                user_valves = UserValves(**form_data)
                Tools.update_user_valves_by_id_and_user_id(
                    id, user.id, user_valves.model_dump()
                )
                return user_valves.model_dump()
            except Exception as e:
                log.exception(f"Failed to update user valves by id {id}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(str(e)),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
