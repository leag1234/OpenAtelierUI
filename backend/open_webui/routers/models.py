import base64
import binascii
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from open_webui.models.models import (
    ModelAccessListResponse,
    ModelAccessResponse,
    ModelExportResponse,
    ModelForm,
    ModelModel,
    ModelResponse,
    ModelUserResponse,
    Models,
)
from open_webui.models.users import Users
from open_webui.utils.imported_history import (
    HistoryNotFound,
    HistoryUnavailable,
    get_history as get_imported_model_history,
    get_current_version_id as get_imported_model_current_version_id,
    list_history as list_imported_model_history,
)
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel


from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_access, has_permission
from open_webui.utils.access_grants import access_control_for_grants, access_grants_for_acl
from open_webui.utils.model_compat import resolve_model_update_id
from open_webui.utils.model_read_compat import (
    filter_models,
    local_model_version_id,
    model_tag_names,
    paginate,
)
from open_webui.env import DATA_DIR, DATABASE_URL, STATIC_DIR


router = APIRouter()


def _model_access_fields(model, user):
    owner_or_admin = model.user_id == user.id or user.role == "admin"
    return {
        "access_grants": access_grants_for_acl(
            model.access_control, "model", model.id, model.created_at
        ),
        "write_access": owner_or_admin
        or has_access(user.id, "write", model.access_control),
    }


def _visible_models(user):
    if user.role == "admin":
        return Models.get_models()
    return Models.get_models_by_user_id(user.id, permission="read")


def _current_version_id(model):
    try:
        return get_imported_model_current_version_id(
            DATA_DIR, DATABASE_URL, "model", model
        )
    except (HistoryNotFound, HistoryUnavailable):
        return local_model_version_id(model)


def _model_owner(model):
    owner = Users.get_user_by_id(model.user_id)
    if owner is None:
        return None
    return {
        "id": owner.id,
        "name": owner.name,
        "role": owner.role,
        "email": owner.email,
    }


def _model_list_response(model, user):
    response = model.model_dump()
    response["meta"].pop("profile_image_url", None)
    response.update(_model_access_fields(model, user))
    response["user"] = _model_owner(model)
    response["version_id"] = _current_version_id(model)
    if user.role != "admin" and model.user_id != user.id:
        response["params"] = {}
    return ModelAccessResponse.model_validate(response)


def _model_export_response(model, user):
    response = model.model_dump()
    response.update(_model_access_fields(model, user))
    response["version_id"] = _current_version_id(model)
    response["background_image_data"] = None
    return ModelExportResponse.model_validate(response)


###########################
# GetModels
###########################


@router.get("/")
async def get_models(id: Optional[str] = None, user=Depends(get_verified_user)):
    if user.role == "admin":
        models = Models.get_models()
    else:
        models = Models.get_models_by_user_id(user.id)
    responses = []
    for model in models:
        response = model.model_dump()
        response.update(_model_access_fields(model, user))
        response["user"] = None
        responses.append(response)
    return responses


###########################
# GetBaseModels
###########################


@router.get("/base", response_model=list[ModelResponse])
async def get_base_models(user=Depends(get_admin_user)):
    return Models.get_base_models()


@router.get("/base/tags", response_model=list[str])
async def get_base_model_tags(user=Depends(get_admin_user)):
    return sorted(
        {tag for model in Models.get_base_models() for tag in model_tag_names(model)}
    )


@router.get("/export", response_model=list[ModelExportResponse])
async def export_models(request: Request, user=Depends(get_verified_user)):
    if user.role != "admin" and not has_permission(
        user.id,
        "workspace.models_export",
        request.app.state.config.USER_PERMISSIONS,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )
    return [_model_export_response(model, user) for model in _visible_models(user)]


@router.get("/list", response_model=ModelAccessListResponse)
async def list_models(
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    tag: Optional[str] = None,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
):
    # The two pinned references currently accept view_option without changing
    # this local projection. Keep the parameter for wire compatibility.
    _ = view_option
    values = filter_models(
        _visible_models(user),
        query=query,
        tag=tag,
        order_by=order_by,
        direction=direction,
    )
    total = len(values)
    return ModelAccessListResponse(
        items=[_model_list_response(model, user) for model in paginate(values, page)],
        total=total,
    )


@router.get("/tags", response_model=list[str])
async def get_model_tags(user=Depends(get_verified_user)):
    return sorted(
        {tag for model in _visible_models(user) for tag in model_tag_names(model)}
    )


############################
# CreateNewModel
############################


@router.post("/create")
async def create_new_model(
    request: Request,
    form_data: ModelForm,
    user=Depends(get_verified_user),
):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.models", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    model = Models.get_model_by_id(form_data.id)
    if model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.MODEL_ID_TAKEN,
        )

    else:
        model = Models.insert_new_model(form_data, user.id)
        if model:
            response = model.model_dump()
            response.update(_model_access_fields(model, user))
            response["user"] = None
            return response
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.DEFAULT(),
            )


###########################
# GetModelById
###########################


# Note: We're not using the typical url path param here, but instead using a query parameter to allow '/' in the id
@router.get("/model")
async def get_model_by_id(id: str, user=Depends(get_verified_user)):
    model = Models.get_model_by_id(id)
    if model:
        if (
            user.role == "admin"
            or model.user_id == user.id
            or has_access(user.id, "read", model.access_control)
        ):
            response = model.model_dump()
            response.update(_model_access_fields(model, user))
            response["user"] = None
            if user.role != "admin" and model.user_id != user.id:
                response["params"] = {}
            response["version_id"] = None
            try:
                response["version_id"] = get_imported_model_current_version_id(
                    DATA_DIR, DATABASE_URL, "model", model
                )
            except (HistoryNotFound, HistoryUnavailable):
                pass
            return response
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get("/model/profile/image")
async def get_model_profile_image(id: str, user=Depends(get_verified_user)):
    model = Models.get_model_by_id(id)
    if model:
        if not (
            user.role == "admin"
            or model.user_id == user.id
            or has_access(user.id, "read", model.access_control)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        image_url = model.meta.profile_image_url or "/static/favicon.png"
    else:
        # Existing clients request the avatar before a model preset has been
        # created. Return the local default without disclosing model state.
        image_url = "/static/favicon.png"
    if image_url.startswith("data:image/"):
        try:
            header, encoded = image_url.split(",", 1)
            if not header.endswith(";base64"):
                raise ValueError("unsupported image encoding")
            content = base64.b64decode(encoded, validate=True)
            if len(content) > 2 * 1024 * 1024:
                raise ValueError("image is too large")
            return Response(content=content, media_type=header[5:-7])
        except (ValueError, binascii.Error):
            raise HTTPException(status_code=400, detail="Invalid model profile image.")

    if image_url.startswith("/static/"):
        root = Path(STATIC_DIR).resolve()
        image_path = (root / unquote(image_url[len("/static/") :])).resolve()
        try:
            image_path.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
        if not image_path.is_file():
            raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
        return FileResponse(image_path)

    if image_url.startswith(("/", "https://", "http://")):
        return RedirectResponse(image_url)
    raise HTTPException(status_code=400, detail="Invalid model profile image.")


@router.get(
    "/model/history",
    operation_id="get_model_history_api_v1_models_model_history_get",
)
async def list_imported_model_versions(id: str, page: int = 1, user=Depends(get_verified_user)):
    model = Models.get_model_by_id(id)
    if not model:
        raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    if user.role != "admin" and model.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    try:
        return list_imported_model_history(DATA_DIR, DATABASE_URL, "model", model, page, Users.get_user_by_id)
    except HistoryNotFound:
        raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    except HistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))


@router.get(
    "/model/history/{version_id}",
    operation_id="get_model_history_version_api_v1_models_model_history__version_id__get",
)
async def read_imported_model_version(id: str, version_id: str, user=Depends(get_verified_user)):
    model = Models.get_model_by_id(id)
    if not model:
        raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    if user.role != "admin" and model.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    try:
        return get_imported_model_history(DATA_DIR, DATABASE_URL, "model", model, version_id, Users.get_user_by_id)
    except HistoryNotFound:
        raise HTTPException(status_code=404, detail="Model version not found")
    except HistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))


@router.post(
    "/model/access/update",
    operation_id="update_model_access_by_id_api_v1_models_model_access_update_post",
)
async def apply_model_access_grants(
    form_data: dict, user=Depends(get_verified_user)
):
    id = form_data.get("id")
    if not isinstance(id, str) or not id.strip():
        raise HTTPException(status_code=422, detail="id must be a nonempty string")
    model = Models.get_model_by_id(id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    if model.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    try:
        access_control = access_control_for_grants(form_data.get("access_grants"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    model = Models.update_model_access_control_by_id(id, access_control)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error updating model access"),
        )
    response = model.model_dump()
    response.update(_model_access_fields(model, user))
    response["user"] = None
    return response


############################
# ToggelModelById
############################


@router.post("/model/toggle", response_model=Optional[ModelResponse])
async def toggle_model_by_id(id: str, user=Depends(get_verified_user)):
    model = Models.get_model_by_id(id)
    if model:
        if (
            user.role == "admin"
            or model.user_id == user.id
            or has_access(user.id, "write", model.access_control)
        ):
            model = Models.toggle_model_by_id(id)

            if model:
                return model
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error updating function"),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.UNAUTHORIZED,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateModelById
############################


@router.post("/model/update")
async def update_model_by_id(
    form_data: ModelForm,
    id: Optional[str] = None,
    user=Depends(get_verified_user),
):
    try:
        id = resolve_model_update_id(id, form_data.id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Model id in query and request body must match",
        ) from None
    model = Models.get_model_by_id(id)

    if not model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        model.user_id != user.id
        and not has_access(user.id, "write", model.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    form_data.access_control = model.access_control
    model = Models.update_model_by_id(id, form_data)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error updating model"),
        )
    response = model.model_dump()
    response.update(_model_access_fields(model, user))
    response["user"] = None
    return response


############################
# DeleteModelById
############################


class ModelIdForm(BaseModel):
    id: str


async def _delete_model_by_id(id, user):
    model = Models.get_model_by_id(id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        user.role != "admin"
        and model.user_id != user.id
        and not has_access(user.id, "write", model.access_control)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    result = Models.delete_model_by_id(id)
    return result


@router.post("/model/delete", response_model=bool)
async def delete_model_by_id_post(
    form_data: ModelIdForm, user=Depends(get_verified_user)
):
    return await _delete_model_by_id(form_data.id, user)


@router.delete("/model/delete", response_model=bool)
async def delete_model_by_id(id: str, user=Depends(get_verified_user)):
    return await _delete_model_by_id(id, user)


@router.delete("/delete/all", response_model=bool)
async def delete_all_models(user=Depends(get_admin_user)):
    result = Models.delete_all_models()
    return result
