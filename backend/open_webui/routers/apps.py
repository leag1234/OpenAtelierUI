# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured Apps administration and interaction compatibility routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from open_webui.config import ENABLE_APPS
from open_webui.models.apps import (
    AppAccessResponse,
    AppConnectionForm,
    AppForm,
    AppInteractionForm,
    AppManifestForm,
    AppResponse,
    AppUpdateForm,
    Apps,
    Manifest,
)
from open_webui.models.messages import Messages
from open_webui.utils.apps_runtime import AppConnections, AppProtocolError, fetch_manifest
from open_webui.utils.auth import get_admin_user, get_verified_user


router = APIRouter()


def apps_enabled() -> bool:
    return bool(ENABLE_APPS.value)


def require_enabled():
    if not apps_enabled():
        raise HTTPException(status_code=404, detail="Apps are disabled")


def with_status(app: AppResponse) -> AppResponse:
    return app.model_copy(update={"connection_status": AppConnections.status(app.id)})


def protocol_error(error: AppProtocolError):
    raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get("", response_model=list[AppResponse])
async def get_apps(user=Depends(get_verified_user)):
    require_enabled()
    return [with_status(app) for app in Apps.list()]


@router.post("/manifest/preview", response_model=Manifest)
async def preview_app_manifest(
    form_data: AppManifestForm,
    user=Depends(get_admin_user),
):
    require_enabled()
    if form_data.api_key is None:
        raise HTTPException(status_code=400, detail="An app API key is required")
    try:
        return await fetch_manifest(form_data.url, form_data.api_key.get_secret_value())
    except AppProtocolError as error:
        protocol_error(error)


@router.post("", response_model=AppResponse)
async def create_new_app(form_data: AppForm, user=Depends(get_admin_user)):
    require_enabled()
    try:
        from open_webui.utils.apps_runtime import validate_app_url

        await validate_app_url(form_data.url)
    except AppProtocolError as error:
        raise HTTPException(status_code=400, detail="Invalid app URL") from error
    try:
        return with_status(Apps.create(form_data))
    except IntegrityError as error:
        raise HTTPException(status_code=400, detail="App slug is already registered") from error


@router.get("/{app_id}", response_model=AppAccessResponse)
async def get_app_details_by_id(app_id: str, user=Depends(get_admin_user)):
    require_enabled()
    app = Apps.access(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    return app.model_copy(update={"connection_status": AppConnections.status(app.id)})


@router.patch("/{app_id}", response_model=AppResponse)
async def update_app_by_id(
    app_id: str,
    form_data: AppUpdateForm,
    user=Depends(get_admin_user),
):
    require_enabled()
    current = Apps.get(app_id)
    if current is None:
        raise HTTPException(status_code=404, detail="App not found")
    if form_data.is_active is True and not current.is_active:
        raise HTTPException(status_code=400, detail="Connect the app to enable it")
    if form_data.is_active is False:
        await AppConnections.disconnect(app_id)
    updated = Apps.update(app_id, form_data)
    return with_status(updated)


@router.delete("/{app_id}", response_model=AppResponse)
async def uninstall_app_by_id(app_id: str, user=Depends(get_admin_user)):
    require_enabled()
    if Apps.get(app_id) is None:
        raise HTTPException(status_code=404, detail="App not found")
    await AppConnections.disconnect(app_id)
    return with_status(Apps.uninstall(app_id))


@router.post("/{app_id}/connect", response_model=AppResponse)
async def connect_app_by_id(
    app_id: str,
    form_data: AppConnectionForm,
    user=Depends(get_admin_user),
):
    require_enabled()
    if Apps.get(app_id) is None:
        raise HTTPException(status_code=404, detail="App not found")
    try:
        api_key = form_data.api_key.get_secret_value() if form_data.api_key else None
        connected = await AppConnections.connect(app_id, api_key)
        return with_status(connected)
    except AppProtocolError as error:
        protocol_error(error)


@router.post("/{app_id}/interactions")
async def create_app_interaction(
    app_id: str,
    form_data: AppInteractionForm,
    user=Depends(get_verified_user),
):
    require_enabled()
    app = Apps.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    if not app.is_active:
        raise HTTPException(status_code=403, detail="App is inactive")
    message = Messages.get_message_by_id(form_data.message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="App message not found")
    message_app_id = (message.data or {}).get("app_id") or (message.meta or {}).get(
        "app_id"
    )
    if message_app_id != app_id:
        raise HTTPException(status_code=404, detail="App message not found")
    try:
        return await AppConnections.request(
            app_id,
            {
                "operation": "channel.action",
                "message_id": form_data.message_id,
                "action_id": form_data.action_id,
                "user_id": user.id,
            },
        )
    except AppProtocolError as error:
        protocol_error(error)
