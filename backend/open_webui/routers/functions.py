import os
import logging
from pathlib import Path
from typing import Annotated, Any, Optional

from open_webui.models.functions import (
    FunctionForm,
    FunctionModel,
    FunctionResponse,
    Functions,
)
from open_webui.models.users import Users
from open_webui.utils.imported_history import (
    HistoryNotFound,
    HistoryUnavailable,
    get_current_function_metadata,
    get_history as get_imported_function_history,
    list_history as list_imported_function_history,
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
from open_webui.utils.plugin import load_function_module_by_id, replace_imports
from open_webui.utils.bounded_url_loader import LoadUrlForm, load_source_url
from open_webui.config import CACHE_DIR
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, WithJsonSchema
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.env import DATA_DIR, DATABASE_URL, SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


router = APIRouter()
_FUNCTION_HISTORY_READS = os.environ.get("OPEN_WEBUI_IMPORTED_HISTORY_READS") == "1"
JsonObject = Annotated[
    dict[str, Any], WithJsonSchema({"type": "object", "additionalProperties": True})
]


class FunctionWithValvesModel(FunctionModel):
    user_id: Optional[str] = None
    valves: Optional[dict[str, Any]] = None


class SyncFunctionsForm(BaseModel):
    functions: list[FunctionWithValvesModel] = []


class FunctionVersionForm(BaseModel):
    version_id: str


def _function_snapshot(function) -> dict:
    meta = function.meta.model_dump(exclude={"manifest"})
    return {"name": function.name, "content": function.content, "meta": meta}


def _function_current_version(function) -> Optional[str]:
    try:
        return current_version_id(DATA_DIR, "function", function.id, function.user_id)
    except VersionHistoryNotFound:
        try:
            return get_current_function_metadata(
                DATA_DIR, DATABASE_URL, function
            )["version_id"]
        except (HistoryNotFound, HistoryUnavailable):
            return None


def _record_function(function, user_id: str, commit_message: Optional[str]) -> str:
    return append_version(
        DATA_DIR,
        "function",
        function.id,
        function.user_id,
        _function_snapshot(function),
        user_id,
        commit_message,
    )


@router.post("/load/url", response_model=Optional[JsonObject])
async def load_function_from_url(
    form_data: LoadUrlForm, user=Depends(get_admin_user)
):
    try:
        content, name = await load_source_url(str(form_data.url))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return {"content": content, "name": name}


@router.post("/sync", response_model=list[FunctionWithValvesModel])
async def sync_functions(
    request: Request,
    form_data: SyncFunctionsForm,
    user=Depends(get_admin_user),
):
    synced = []
    for incoming in form_data.functions:
        content = replace_imports(incoming.content)
        module, function_type, frontmatter = load_function_module_by_id(
            incoming.id, content=content
        )
        meta = incoming.meta.model_copy(update={"manifest": frontmatter})
        form = FunctionForm(
            id=incoming.id,
            name=incoming.name,
            content=content,
            meta=meta,
        )
        existing = Functions.get_function_by_id(incoming.id)
        if existing is None:
            Functions.insert_new_function(user.id, function_type, form)
        updated = Functions.update_function_by_id(
            incoming.id,
            {
                "user_id": user.id,
                "name": incoming.name,
                "type": function_type,
                "content": content,
                "meta": meta.model_dump(),
                "valves": incoming.valves or {},
                "is_active": incoming.is_active,
                "is_global": incoming.is_global,
                "created_at": incoming.created_at,
            },
        )
        if updated is None:
            raise HTTPException(status_code=400, detail="Error syncing function")
        request.app.state.FUNCTIONS[incoming.id] = module
        synced.append(
            FunctionWithValvesModel(
                **updated.model_dump(),
                valves=Functions.get_function_valves_by_id(incoming.id) or {},
            )
        )
    return synced

############################
# GetFunctions
############################


@router.get("/list", response_model=list[FunctionResponse])
@router.get("/", response_model=list[FunctionResponse])
async def get_functions(user=Depends(get_verified_user)):
    return Functions.get_functions()


############################
# ExportFunctions
############################


@router.get("/export", response_model=list[FunctionModel])
async def get_functions(user=Depends(get_admin_user)):
    return Functions.get_functions()


############################
# CreateNewFunction
############################


@router.post("/create", response_model=Optional[FunctionResponse])
async def create_new_function(
    request: Request, form_data: FunctionForm, user=Depends(get_admin_user)
):
    if not form_data.id.isidentifier():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only alphanumeric characters and underscores are allowed in the id",
        )

    form_data.id = form_data.id.lower()

    function = Functions.get_function_by_id(form_data.id)
    if function is None:
        try:
            form_data.content = replace_imports(form_data.content)
            function_module, function_type, frontmatter = load_function_module_by_id(
                form_data.id,
                content=form_data.content,
            )
            form_data.meta.manifest = frontmatter

            FUNCTIONS = request.app.state.FUNCTIONS
            FUNCTIONS[form_data.id] = function_module

            function = Functions.insert_new_function(user.id, function_type, form_data)

            function_cache_dir = CACHE_DIR / "functions" / form_data.id
            function_cache_dir.mkdir(parents=True, exist_ok=True)

            if function:
                function.version_id = _record_function(
                    function, user.id, form_data.commit_message
                )
                return function
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error creating function"),
                )
        except Exception as e:
            log.exception(f"Failed to create a new function: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(e),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )


############################
# GetFunctionById
############################


@router.get(
    "/id/{id}",
    response_model=None if _FUNCTION_HISTORY_READS else Optional[FunctionModel],
)
async def get_function_by_id(id: str, user=Depends(get_admin_user)):
    function = Functions.get_function_by_id(id)

    if function:
        native_reader = globals().get("_function_current_version")
        native_version_id = native_reader(function) if native_reader else None
        if _FUNCTION_HISTORY_READS:
            response = function.model_dump()
            response["version_id"] = native_version_id
            if native_version_id is None:
                try:
                    metadata = get_current_function_metadata(
                        DATA_DIR, DATABASE_URL, function
                    )
                except HistoryUnavailable as error:
                    raise HTTPException(status_code=503, detail=str(error))
                response["version_id"] = metadata["version_id"]
                if "toggle" in metadata:
                    response.setdefault("meta", {})["toggle"] = metadata["toggle"]
            return response
        function.version_id = native_version_id
        return function
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


async def get_function_history(id: str, page: int = Query(default=1), user=Depends(get_admin_user)):
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    function = Functions.get_function_by_id(id)
    if not function:
        raise HTTPException(status_code=404, detail="Not found")
    native_reader = globals().get("list_versions")
    if native_reader:
        try:
            return native_reader(
                DATA_DIR, "function", function.id, function.user_id, page, Users.get_user_by_id
            )
        except Exception as error:
            if isinstance(error, globals()["VersionHistoryUnavailable"]):
                raise HTTPException(status_code=503, detail=str(error))
            if not isinstance(error, globals()["VersionHistoryNotFound"]):
                raise
    try:
        return list_imported_function_history(
            DATA_DIR, DATABASE_URL, "function", function, page, Users.get_user_by_id
        )
    except HistoryNotFound:
        raise HTTPException(status_code=404, detail="Not found")
    except HistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))


async def get_function_history_version(
    id: str, history_id: str, user=Depends(get_admin_user)
):
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    function = Functions.get_function_by_id(id)
    if not function:
        raise HTTPException(status_code=404, detail="Not found")
    native_reader = globals().get("get_version")
    if native_reader:
        try:
            return native_reader(
                DATA_DIR,
                "function",
                function.id,
                function.user_id,
                history_id,
                Users.get_user_by_id,
            )
        except Exception as error:
            if isinstance(error, globals()["VersionHistoryUnavailable"]):
                raise HTTPException(status_code=503, detail=str(error))
            if not isinstance(error, globals()["VersionHistoryNotFound"]):
                raise
    try:
        return get_imported_function_history(
            DATA_DIR, DATABASE_URL, "function", function, history_id, Users.get_user_by_id
        )
    except HistoryNotFound:
        raise HTTPException(status_code=404, detail="Version not found")
    except HistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))


router.add_api_route(
    "/id/{id}/history",
    get_function_history,
    methods=["GET"],
    operation_id="get_function_history_api_v1_functions_id__id__history_get",
)


@router.get(
    "/id/{id}/history/diff",
    operation_id="get_function_history_diff_api_v1_functions_id__id__history_diff_get",
)
async def get_function_history_diff(
    id: str,
    from_id: str,
    to_id: str,
    user=Depends(get_admin_user),
):
    function = Functions.get_function_by_id(id)
    if not function:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return compare_versions(
            DATA_DIR,
            "function",
            function.id,
            function.user_id,
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
    operation_id="delete_function_history_entry_api_v1_functions_id__id__history__history_id__delete",
)
async def delete_function_history_entry(
    id: str, history_id: str, user=Depends(get_admin_user)
):
    function = Functions.get_function_by_id(id)
    if not function:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return delete_version(
            DATA_DIR, "function", function.id, function.user_id, history_id
        )
    except VersionHistoryNotFound:
        raise HTTPException(status_code=404, detail="Version not found")
    except VersionHistoryUnavailable as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post(
    "/id/{id}/update/version",
    response_model=FunctionModel,
    operation_id="set_function_production_api_v1_functions_id__id__update_version_post",
)
async def set_function_production(
    request: Request,
    id: str,
    form_data: FunctionVersionForm,
    user=Depends(get_admin_user),
):
    function = Functions.get_function_by_id(id)
    if not function:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        entry = get_version(
            DATA_DIR,
            "function",
            function.id,
            function.user_id,
            form_data.version_id,
            Users.get_user_by_id,
        )
        snapshot = entry["snapshot"]
        selected = FunctionForm(id=id, **snapshot)
        selected.content = replace_imports(selected.content)
        module, function_type, frontmatter = load_function_module_by_id(
            id, content=selected.content
        )
        selected.meta.manifest = frontmatter
        request.app.state.FUNCTIONS[id] = module
        updated = Functions.update_function_by_id(
            id,
            {
                **selected.model_dump(exclude={"id", "commit_message"}),
                "type": function_type,
            },
        )
        if not updated:
            raise VersionHistoryUnavailable("Error selecting function version")
        select_version(
            DATA_DIR, "function", function.id, function.user_id, form_data.version_id
        )
        response = updated.model_dump(mode="json")
        response["version_id"] = form_data.version_id
        response.setdefault("meta", {})["toggle"] = False
        return JSONResponse(content=response)
    except VersionHistoryNotFound:
        raise HTTPException(status_code=404, detail="Version not found")
    except (VersionHistoryUnavailable, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error))


router.add_api_route(
    "/id/{id}/history/{history_id}",
    get_function_history_version,
    methods=["GET"],
    operation_id="get_function_history_entry_api_v1_functions_id__id__history__history_id__get",
    summary="Get Function History Entry",
)


############################
# ToggleFunctionById
############################


@router.post("/id/{id}/toggle", response_model=Optional[FunctionModel])
async def toggle_function_by_id(id: str, user=Depends(get_admin_user)):
    function = Functions.get_function_by_id(id)
    if function:
        function = Functions.update_function_by_id(
            id, {"is_active": not function.is_active}
        )

        if function:
            return function
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating function"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# ToggleGlobalById
############################


@router.post("/id/{id}/toggle/global", response_model=Optional[FunctionModel])
async def toggle_global_by_id(id: str, user=Depends(get_admin_user)):
    function = Functions.get_function_by_id(id)
    if function:
        function = Functions.update_function_by_id(
            id, {"is_global": not function.is_global}
        )

        if function:
            return function
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating function"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateFunctionById
############################


@router.post("/id/{id}/update", response_model=Optional[FunctionModel])
async def update_function_by_id(
    request: Request, id: str, form_data: FunctionForm, user=Depends(get_admin_user)
):
    try:
        existing = Functions.get_function_by_id(id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Not found")
        ensure_root(
            DATA_DIR,
            "function",
            existing.id,
            existing.user_id,
            _function_snapshot(existing),
            user.id,
        )
        form_data.content = replace_imports(form_data.content)
        function_module, function_type, frontmatter = load_function_module_by_id(
            id, content=form_data.content
        )
        form_data.meta.manifest = frontmatter

        FUNCTIONS = request.app.state.FUNCTIONS
        FUNCTIONS[id] = function_module

        updated = {
            **form_data.model_dump(exclude={"id", "commit_message"}),
            "type": function_type,
        }
        log.debug(updated)

        function = Functions.update_function_by_id(id, updated)

        if function:
            function.version_id = _record_function(
                function, user.id, form_data.commit_message
            )
            return function
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating function"),
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# DeleteFunctionById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_function_by_id(
    request: Request, id: str, user=Depends(get_admin_user)
):
    result = Functions.delete_function_by_id(id)

    if result:
        FUNCTIONS = request.app.state.FUNCTIONS
        if id in FUNCTIONS:
            del FUNCTIONS[id]

    return result


############################
# GetFunctionValves
############################


@router.get("/id/{id}/valves", response_model=Optional[dict])
async def get_function_valves_by_id(id: str, user=Depends(get_admin_user)):
    function = Functions.get_function_by_id(id)
    if function:
        try:
            valves = Functions.get_function_valves_by_id(id)
            return valves
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(e),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# GetFunctionValvesSpec
############################


@router.get("/id/{id}/valves/spec", response_model=Optional[dict])
async def get_function_valves_spec_by_id(
    request: Request, id: str, user=Depends(get_admin_user)
):
    function = Functions.get_function_by_id(id)
    if function:
        if id in request.app.state.FUNCTIONS:
            function_module = request.app.state.FUNCTIONS[id]
        else:
            function_module, function_type, frontmatter = load_function_module_by_id(id)
            request.app.state.FUNCTIONS[id] = function_module

        if hasattr(function_module, "Valves"):
            Valves = function_module.Valves
            return Valves.schema()
        return None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateFunctionValves
############################


@router.post("/id/{id}/valves/update", response_model=Optional[dict])
async def update_function_valves_by_id(
    request: Request, id: str, form_data: dict, user=Depends(get_admin_user)
):
    function = Functions.get_function_by_id(id)
    if function:
        if id in request.app.state.FUNCTIONS:
            function_module = request.app.state.FUNCTIONS[id]
        else:
            function_module, function_type, frontmatter = load_function_module_by_id(id)
            request.app.state.FUNCTIONS[id] = function_module

        if hasattr(function_module, "Valves"):
            Valves = function_module.Valves

            try:
                form_data = {k: v for k, v in form_data.items() if v is not None}
                valves = Valves(**form_data)
                Functions.update_function_valves_by_id(id, valves.model_dump())
                return valves.model_dump()
            except Exception as e:
                log.exception(f"Error updating function values by id {id}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(e),
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


############################
# FunctionUserValves
############################


@router.get("/id/{id}/valves/user", response_model=Optional[dict])
async def get_function_user_valves_by_id(id: str, user=Depends(get_verified_user)):
    function = Functions.get_function_by_id(id)
    if function:
        try:
            user_valves = Functions.get_user_valves_by_id_and_user_id(id, user.id)
            return user_valves
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(e),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get("/id/{id}/valves/user/spec", response_model=Optional[dict])
async def get_function_user_valves_spec_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    function = Functions.get_function_by_id(id)
    if function:
        if id in request.app.state.FUNCTIONS:
            function_module = request.app.state.FUNCTIONS[id]
        else:
            function_module, function_type, frontmatter = load_function_module_by_id(id)
            request.app.state.FUNCTIONS[id] = function_module

        if hasattr(function_module, "UserValves"):
            UserValves = function_module.UserValves
            return UserValves.schema()
        return None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.post("/id/{id}/valves/user/update", response_model=Optional[dict])
async def update_function_user_valves_by_id(
    request: Request, id: str, form_data: dict, user=Depends(get_verified_user)
):
    function = Functions.get_function_by_id(id)

    if function:
        if id in request.app.state.FUNCTIONS:
            function_module = request.app.state.FUNCTIONS[id]
        else:
            function_module, function_type, frontmatter = load_function_module_by_id(id)
            request.app.state.FUNCTIONS[id] = function_module

        if hasattr(function_module, "UserValves"):
            UserValves = function_module.UserValves

            try:
                form_data = {k: v for k, v in form_data.items() if v is not None}
                user_valves = UserValves(**form_data)
                Functions.update_user_valves_by_id_and_user_id(
                    id, user.id, user_valves.model_dump()
                )
                return user_valves.model_dump()
            except Exception as e:
                log.exception(f"Error updating function user valves by id {id}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(e),
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
