from typing import Iterator, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
import json
import logging
import os
from pathlib import PurePosixPath
import tempfile
import time
import uuid
import zipfile

from open_webui.models.knowledge import (
    Knowledges,
    KnowledgeForm,
    KnowledgeResponse,
    KnowledgeUserResponse,
    KnowledgeAccessListResponse,
    KnowledgeAccessResponse,
    KnowledgeDirectoryCreateForm,
    KnowledgeDirectoryModel,
    KnowledgeDirectoryUpdateForm,
    KnowledgeFileMoveForm,
    SyncCleanupForm,
    SyncDiffForm,
    SyncDiffResponse,
    knowledge_access_control_for_grants,
    knowledge_access_grants_for_acl,
)
from open_webui.models.files import Files, FileMeta, FileModel
from open_webui.models.users import Users, UserResponse
from open_webui.retrieval.vector.connector import VECTOR_DB_CLIENT
from open_webui.routers.retrieval import (
    process_file,
    ProcessFileForm,
    process_files_batch,
    BatchProcessFilesForm,
)
from open_webui.storage.provider import Storage

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.auth import get_verified_user
from open_webui.utils.access_control import has_access, has_permission


from open_webui.env import SRC_LOG_LEVELS
from open_webui.models.models import Models, ModelForm


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


class FileUserResponse(BaseModel):
    id: str
    user_id: str
    filename: str
    created_at: int
    hash: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[FileMeta] = None
    updated_at: Optional[int] = None
    user: Optional[UserResponse] = None
    directory_id: Optional[str] = None
    directory_path: str = ""
    has_original: bool = True


class KnowledgeFileListResponse(BaseModel):
    items: list[FileUserResponse]
    total: int
    directories: list[KnowledgeDirectoryModel] = Field(default_factory=list)
    breadcrumbs: list[KnowledgeDirectoryModel] = Field(default_factory=list)


def _knowledge_data(knowledge) -> dict:
    return dict(knowledge.data) if isinstance(knowledge.data, dict) else {}


def _directory_models(knowledge) -> list[KnowledgeDirectoryModel]:
    result = []
    for value in _knowledge_data(knowledge).get("directories", []):
        try:
            result.append(KnowledgeDirectoryModel.model_validate(value))
        except Exception:
            continue
    return result


def _directory_map(knowledge) -> dict[str, KnowledgeDirectoryModel]:
    return {directory.id: directory for directory in _directory_models(knowledge)}


def _file_directories(knowledge) -> dict[str, Optional[str]]:
    values = _knowledge_data(knowledge).get("file_directories", {})
    if not isinstance(values, dict):
        return {}
    return {
        file_id: directory_id
        for file_id, directory_id in values.items()
        if isinstance(file_id, str)
        and (directory_id is None or isinstance(directory_id, str))
    }


def _can_read_knowledge(knowledge, user) -> bool:
    return (
        user.role == "admin"
        or knowledge.user_id == user.id
        or has_access(user.id, "read", knowledge.access_control)
    )


def _can_write_knowledge(knowledge, user) -> bool:
    return (
        user.role == "admin"
        or knowledge.user_id == user.id
        or has_access(user.id, "write", knowledge.access_control)
    )


def _directory_path(directory_id, directories) -> str:
    if not directory_id:
        return ""
    parts = []
    seen = set()
    current = directories.get(directory_id)
    while current and current.id not in seen:
        seen.add(current.id)
        parts.append(current.name)
        current = directories.get(current.parent_id) if current.parent_id else None
    return "/".join(reversed(parts))


def _directory_breadcrumbs(directory_id, directories):
    if not directory_id:
        return []
    result = []
    seen = set()
    current = directories.get(directory_id)
    while current and current.id not in seen:
        seen.add(current.id)
        result.append(current)
        current = directories.get(current.parent_id) if current.parent_id else None
    return list(reversed(result))


def _file_response(file, knowledge, directories=None, file_directories=None):
    directories = directories if directories is not None else _directory_map(knowledge)
    file_directories = (
        file_directories
        if file_directories is not None
        else _file_directories(knowledge)
    )
    directory_id = file_directories.get(file.id)
    user = Users.get_user_by_id(file.user_id)
    return FileUserResponse(
        id=file.id,
        user_id=file.user_id,
        filename=file.filename,
        hash=file.hash,
        data=file.data,
        meta=FileMeta.model_validate(file.meta or {}),
        created_at=int(file.created_at or 0),
        updated_at=file.updated_at,
        user=UserResponse.model_validate(user.model_dump()) if user else None,
        directory_id=directory_id,
        directory_path=_directory_path(directory_id, directories),
        has_original=bool(file.path),
    )


def _validate_directory_name(name: str) -> str:
    value = name.strip() if isinstance(name, str) else ""
    if (
        not value
        or len(value) > 255
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise HTTPException(status_code=400, detail="Invalid directory name")
    return value


def _validate_manifest_path(path: str) -> str:
    value = PurePosixPath(path)
    if (
        not path
        or value.is_absolute()
        or any(part in ("", ".", "..") for part in value.parts)
    ):
        raise HTTPException(status_code=400, detail="Invalid manifest path")
    return value.as_posix()


def _knowledge_access_response(knowledge, user) -> KnowledgeAccessResponse:
    file_ids = _knowledge_data(knowledge).get("file_ids", [])
    owner = Users.get_user_by_id(knowledge.user_id)
    return KnowledgeAccessResponse(
        id=knowledge.id,
        user_id=knowledge.user_id,
        name=knowledge.name,
        description=knowledge.description,
        meta=knowledge.meta,
        user=UserResponse.model_validate(owner.model_dump()) if owner else None,
        file_count=len(file_ids) if isinstance(file_ids, list) else 0,
        write_access=_can_write_knowledge(knowledge, user),
        access_grants=knowledge_access_grants_for_acl(
            knowledge.access_control, "knowledge", knowledge.id, knowledge.created_at
        ),
        created_at=knowledge.created_at,
        updated_at=knowledge.updated_at,
    )


def _save_knowledge_organization(knowledge, directories, file_directories):
    data = _knowledge_data(knowledge)
    data["directories"] = [directory.model_dump() for directory in directories]
    data["file_directories"] = file_directories
    return Knowledges.update_knowledge_data_by_id(knowledge.id, data)


############################
# getKnowledgeBases
############################


@router.get("/", response_model=list[KnowledgeUserResponse])
async def get_knowledge(user=Depends(get_verified_user)):
    knowledge_bases = []

    if user.role == "admin":
        knowledge_bases = Knowledges.get_knowledge_bases()
    else:
        knowledge_bases = Knowledges.get_knowledge_bases_by_user_id(user.id, "read")

    # Get files for each knowledge base
    knowledge_with_files = []
    for knowledge_base in knowledge_bases:
        files = []
        if knowledge_base.data:
            files = Files.get_file_metadatas_by_ids(
                knowledge_base.data.get("file_ids", [])
            )

            # Check if all files exist
            if len(files) != len(knowledge_base.data.get("file_ids", [])):
                missing_files = list(
                    set(knowledge_base.data.get("file_ids", []))
                    - set([file.id for file in files])
                )
                if missing_files:
                    data = knowledge_base.data or {}
                    file_ids = data.get("file_ids", [])

                    for missing_file in missing_files:
                        file_ids.remove(missing_file)

                    data["file_ids"] = file_ids
                    Knowledges.update_knowledge_data_by_id(
                        id=knowledge_base.id, data=data
                    )

                    files = Files.get_file_metadatas_by_ids(file_ids)

        knowledge_with_files.append(
            KnowledgeUserResponse(
                **knowledge_base.model_dump(),
                files=files,
            )
        )

    return knowledge_with_files


@router.get("/list", response_model=list[KnowledgeUserResponse])
async def get_knowledge_list(user=Depends(get_verified_user)):
    knowledge_bases = []

    if user.role == "admin":
        knowledge_bases = Knowledges.get_knowledge_bases()
    else:
        knowledge_bases = Knowledges.get_knowledge_bases_by_user_id(user.id, "write")

    # Get files for each knowledge base
    knowledge_with_files = []
    for knowledge_base in knowledge_bases:
        files = []
        if knowledge_base.data:
            files = Files.get_file_metadatas_by_ids(
                knowledge_base.data.get("file_ids", [])
            )

            # Check if all files exist
            if len(files) != len(knowledge_base.data.get("file_ids", [])):
                missing_files = list(
                    set(knowledge_base.data.get("file_ids", []))
                    - set([file.id for file in files])
                )
                if missing_files:
                    data = knowledge_base.data or {}
                    file_ids = data.get("file_ids", [])

                    for missing_file in missing_files:
                        file_ids.remove(missing_file)

                    data["file_ids"] = file_ids
                    Knowledges.update_knowledge_data_by_id(
                        id=knowledge_base.id, data=data
                    )

                    files = Files.get_file_metadatas_by_ids(file_ids)

        knowledge_with_files.append(
            KnowledgeUserResponse(
                **knowledge_base.model_dump(),
                files=files,
            )
        )
    return knowledge_with_files


@router.get("/search", response_model=KnowledgeAccessListResponse)
async def search_knowledge_bases(
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    source: Optional[str] = None,
    page: Optional[int] = 1,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    user=Depends(get_verified_user),
):
    knowledge_bases = (
        Knowledges.get_knowledge_bases()
        if user.role == "admin"
        else Knowledges.get_knowledge_bases_by_user_id(user.id, "read")
    )
    if view_option == "owned":
        knowledge_bases = [item for item in knowledge_bases if item.user_id == user.id]
    elif view_option == "shared":
        knowledge_bases = [item for item in knowledge_bases if item.user_id != user.id]
    if source and source not in ("all", "local"):
        knowledge_bases = []
    needle = (query or "").casefold().strip()
    if needle:
        knowledge_bases = [
            item
            for item in knowledge_bases
            if needle in item.name.casefold() or needle in item.description.casefold()
        ]
    reverse = (direction or "desc").casefold() != "asc"
    key_name = (
        order_by if order_by in ("name", "created_at", "updated_at") else "updated_at"
    )
    knowledge_bases.sort(
        key=lambda item: (getattr(item, key_name), item.id), reverse=reverse
    )
    total = len(knowledge_bases)
    current_page = max(1, page or 1)
    knowledge_bases = knowledge_bases[(current_page - 1) * 30 : current_page * 30]
    return KnowledgeAccessListResponse(
        items=[_knowledge_access_response(item, user) for item in knowledge_bases],
        total=total,
    )


@router.get("/search/files", response_model=KnowledgeFileListResponse)
async def search_knowledge_files(
    query: Optional[str] = None,
    include_content: bool = False,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
):
    knowledge_bases = (
        Knowledges.get_knowledge_bases()
        if user.role == "admin"
        else Knowledges.get_knowledge_bases_by_user_id(user.id, "read")
    )
    needle = (query or "").casefold().strip()
    items = []
    seen = set()
    for knowledge in knowledge_bases:
        data = _knowledge_data(knowledge)
        file_ids = data.get("file_ids", [])
        if not isinstance(file_ids, list):
            continue
        directories = _directory_map(knowledge)
        file_directories = _file_directories(knowledge)
        for file in Files.get_files_by_ids(file_ids):
            if file.id in seen:
                continue
            searchable = file.filename
            if include_content and isinstance(file.data, dict):
                searchable += " " + str(file.data.get("content", ""))
            if needle and needle not in searchable.casefold():
                continue
            seen.add(file.id)
            items.append(_file_response(file, knowledge, directories, file_directories))
    items.sort(key=lambda item: (item.updated_at or 0, item.id), reverse=True)
    total = len(items)
    current_page = max(1, page or 1)
    return KnowledgeFileListResponse(
        items=items[(current_page - 1) * 30 : current_page * 30], total=total
    )


############################
# CreateNewKnowledge
############################


@router.post("/create", response_model=Optional[KnowledgeResponse])
async def create_new_knowledge(
    request: Request, form_data: KnowledgeForm, user=Depends(get_verified_user)
):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.knowledge", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    knowledge = Knowledges.insert_new_knowledge(user.id, form_data)

    if knowledge:
        return knowledge
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.FILE_EXISTS,
        )


############################
# ReindexKnowledgeFiles
############################


@router.post("/reindex", response_model=bool)
async def reindex_knowledge_files(request: Request, user=Depends(get_verified_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    knowledge_bases = Knowledges.get_knowledge_bases()

    log.info(f"Starting reindexing for {len(knowledge_bases)} knowledge bases")

    for knowledge_base in knowledge_bases:
        try:
            files = Files.get_files_by_ids(knowledge_base.data.get("file_ids", []))

            try:
                if VECTOR_DB_CLIENT.has_collection(collection_name=knowledge_base.id):
                    VECTOR_DB_CLIENT.delete_collection(
                        collection_name=knowledge_base.id
                    )
            except Exception as e:
                log.error(f"Error deleting collection {knowledge_base.id}: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error deleting vector DB collection",
                )

            failed_files = []
            for file in files:
                try:
                    process_file(
                        request,
                        ProcessFileForm(
                            file_id=file.id, collection_name=knowledge_base.id
                        ),
                        user=user,
                    )
                except Exception as e:
                    log.error(
                        f"Error processing file {file.filename} (ID: {file.id}): {str(e)}"
                    )
                    failed_files.append({"file_id": file.id, "error": str(e)})
                    continue

        except Exception as e:
            log.error(f"Error processing knowledge base {knowledge_base.id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error processing knowledge base",
            )

        if failed_files:
            log.warning(
                f"Failed to process {len(failed_files)} files in knowledge base {knowledge_base.id}"
            )
            for failed in failed_files:
                log.warning(f"File ID: {failed['file_id']}, Error: {failed['error']}")

    log.info("Reindexing completed successfully")
    return True


############################
# GetKnowledgeById
############################


class KnowledgeFilesResponse(KnowledgeResponse):
    files: list[FileModel]


@router.get("/{id}", response_model=Optional[KnowledgeFilesResponse])
async def get_knowledge_by_id(id: str, user=Depends(get_verified_user)):
    knowledge = Knowledges.get_knowledge_by_id(id=id)

    if knowledge:

        if (
            user.role == "admin"
            or knowledge.user_id == user.id
            or has_access(user.id, "read", knowledge.access_control)
        ):

            file_ids = knowledge.data.get("file_ids", []) if knowledge.data else []
            files = Files.get_files_by_ids(file_ids)

            return KnowledgeFilesResponse(
                **knowledge.model_dump(),
                files=files,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ERROR_MESSAGES.NOT_FOUND,
    )


############################
# UpdateKnowledgeById
############################


@router.post("/{id}/update", response_model=Optional[KnowledgeFilesResponse])
async def update_knowledge_by_id(
    id: str,
    form_data: KnowledgeForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    # Is the user the original creator, in a group with write access, or an admin
    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    knowledge = Knowledges.update_knowledge_by_id(id=id, form_data=form_data)
    if knowledge:
        file_ids = knowledge.data.get("file_ids", []) if knowledge.data else []
        files = Files.get_files_by_ids(file_ids)

        return KnowledgeFilesResponse(
            **knowledge.model_dump(),
            files=files,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )


@router.post("/{id}/access/update", response_model=Optional[KnowledgeFilesResponse])
async def update_knowledge_access_by_id(
    id: str, form_data: dict, user=Depends(get_verified_user)
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if knowledge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    if knowledge.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    try:
        access_control = knowledge_access_control_for_grants(
            form_data.get("access_grants")
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    knowledge = Knowledges.update_knowledge_access_control_by_id(id, access_control)
    if knowledge is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )
    file_ids = knowledge.data.get("file_ids", []) if knowledge.data else []
    return KnowledgeFilesResponse(
        **knowledge.model_dump(), files=Files.get_files_by_ids(file_ids)
    )


############################
# AddFileToKnowledge
############################


class KnowledgeFileIdForm(BaseModel):
    file_id: str
    directory_id: Optional[str] = None


@router.post("/{id}/file/add", response_model=Optional[KnowledgeFilesResponse])
def add_file_to_knowledge_by_id(
    request: Request,
    id: str,
    form_data: KnowledgeFileIdForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)

    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    if not file.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.FILE_NOT_PROCESSED,
        )

    # Add content to the vector database
    try:
        process_file(
            request,
            ProcessFileForm(file_id=form_data.file_id, collection_name=id),
            user=user,
        )
    except Exception as e:
        log.debug(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    if knowledge:
        data = knowledge.data or {}
        file_ids = data.get("file_ids", [])

        if form_data.file_id not in file_ids:
            directories = _directory_map(knowledge)
            if form_data.directory_id and form_data.directory_id not in directories:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
            file_ids.append(form_data.file_id)
            data["file_ids"] = file_ids
            file_directories = _file_directories(knowledge)
            file_directories[form_data.file_id] = form_data.directory_id
            data["file_directories"] = file_directories

            knowledge = Knowledges.update_knowledge_data_by_id(id=id, data=data)

            if knowledge:
                files = Files.get_files_by_ids(file_ids)

                return KnowledgeFilesResponse(
                    **knowledge.model_dump(),
                    files=files,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("knowledge"),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("file_id"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.post("/{id}/file/update", response_model=Optional[KnowledgeFilesResponse])
def update_file_from_knowledge_by_id(
    request: Request,
    id: str,
    form_data: KnowledgeFileIdForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # Remove content from the vector database
    VECTOR_DB_CLIENT.delete(
        collection_name=knowledge.id, filter={"file_id": form_data.file_id}
    )

    # Add content to the vector database
    try:
        process_file(
            request,
            ProcessFileForm(file_id=form_data.file_id, collection_name=id),
            user=user,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    if knowledge:
        data = knowledge.data or {}
        file_ids = data.get("file_ids", [])

        files = Files.get_files_by_ids(file_ids)

        return KnowledgeFilesResponse(
            **knowledge.model_dump(),
            files=files,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# RemoveFileFromKnowledge
############################


@router.post("/{id}/file/remove", response_model=Optional[KnowledgeFilesResponse])
def remove_file_from_knowledge_by_id(
    id: str,
    form_data: KnowledgeFileIdForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    file = Files.get_file_by_id(form_data.file_id)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # Remove content from the vector database
    try:
        VECTOR_DB_CLIENT.delete(
            collection_name=knowledge.id, filter={"file_id": form_data.file_id}
        )
    except Exception as e:
        log.debug("This was most likely caused by bypassing embedding processing")
        log.debug(e)
        pass

    try:
        # Remove the file's collection from vector database
        file_collection = f"file-{form_data.file_id}"
        if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
            VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
    except Exception as e:
        log.debug("This was most likely caused by bypassing embedding processing")
        log.debug(e)
        pass

    # Delete file from database
    Files.delete_file_by_id(form_data.file_id)

    if knowledge:
        data = knowledge.data or {}
        file_ids = data.get("file_ids", [])

        if form_data.file_id in file_ids:
            file_ids.remove(form_data.file_id)
            data["file_ids"] = file_ids

            knowledge = Knowledges.update_knowledge_data_by_id(id=id, data=data)

            if knowledge:
                files = Files.get_files_by_ids(file_ids)

                return KnowledgeFilesResponse(
                    **knowledge.model_dump(),
                    files=files,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("knowledge"),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("file_id"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# DeleteKnowledgeById
############################


@router.delete("/{id}/delete", response_model=bool)
async def delete_knowledge_by_id(id: str, user=Depends(get_verified_user)):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    log.info(f"Deleting knowledge base: {id} (name: {knowledge.name})")

    # Get all models
    models = Models.get_all_models()
    log.info(f"Found {len(models)} models to check for knowledge base {id}")

    # Update models that reference this knowledge base
    for model in models:
        if model.meta and hasattr(model.meta, "knowledge"):
            knowledge_list = model.meta.knowledge or []
            # Filter out the deleted knowledge base
            updated_knowledge = [k for k in knowledge_list if k.get("id") != id]

            # If the knowledge list changed, update the model
            if len(updated_knowledge) != len(knowledge_list):
                log.info(f"Updating model {model.id} to remove knowledge base {id}")
                model.meta.knowledge = updated_knowledge
                # Create a ModelForm for the update
                model_form = ModelForm(
                    id=model.id,
                    name=model.name,
                    base_model_id=model.base_model_id,
                    meta=model.meta,
                    params=model.params,
                    access_control=model.access_control,
                    is_active=model.is_active,
                )
                Models.update_model_by_id(model.id, model_form)

    # Clean up vector DB
    try:
        VECTOR_DB_CLIENT.delete_collection(collection_name=id)
    except Exception as e:
        log.debug(e)
        pass
    result = Knowledges.delete_knowledge_by_id(id=id)
    return result


############################
# ResetKnowledgeById
############################


@router.post("/{id}/reset", response_model=Optional[KnowledgeResponse])
async def reset_knowledge_by_id(id: str, user=Depends(get_verified_user)):
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    try:
        VECTOR_DB_CLIENT.delete_collection(collection_name=id)
    except Exception as e:
        log.debug(e)
        pass

    knowledge = Knowledges.update_knowledge_data_by_id(id=id, data={"file_ids": []})

    return knowledge


############################
# AddFilesToKnowledge
############################


@router.post("/{id}/files/batch/add", response_model=Optional[KnowledgeFilesResponse])
def add_files_to_knowledge_batch(
    request: Request,
    id: str,
    form_data: list[KnowledgeFileIdForm],
    user=Depends(get_verified_user),
):
    """
    Add multiple files to a knowledge base
    """
    knowledge = Knowledges.get_knowledge_by_id(id=id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    # Get files content
    log.info(f"files/batch/add - {len(form_data)} files")
    files: List[FileModel] = []
    for form in form_data:
        file = Files.get_file_by_id(form.file_id)
        if not file:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File {form.file_id} not found",
            )
        files.append(file)

    # Process files
    try:
        result = process_files_batch(
            request=request,
            form_data=BatchProcessFilesForm(files=files, collection_name=id),
            user=user,
        )
    except Exception as e:
        log.error(
            f"add_files_to_knowledge_batch: Exception occurred: {e}", exc_info=True
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Add successful files to knowledge base
    data = knowledge.data or {}
    existing_file_ids = data.get("file_ids", [])

    # Only add files that were successfully processed
    successful_file_ids = [r.file_id for r in result.results if r.status == "completed"]
    for file_id in successful_file_ids:
        if file_id not in existing_file_ids:
            existing_file_ids.append(file_id)

    data["file_ids"] = existing_file_ids
    knowledge = Knowledges.update_knowledge_data_by_id(id=id, data=data)

    # If there were any errors, include them in the response
    if result.errors:
        error_details = [f"{err.file_id}: {err.error}" for err in result.errors]
        return KnowledgeFilesResponse(
            **knowledge.model_dump(),
            files=Files.get_files_by_ids(existing_file_ids),
            warnings={
                "message": "Some files failed to process",
                "errors": error_details,
            },
        )

    return KnowledgeFilesResponse(
        **knowledge.model_dump(), files=Files.get_files_by_ids(existing_file_ids)
    )


@router.get("/{id}/files", response_model=KnowledgeFileListResponse)
async def get_knowledge_files_by_id(
    id: str,
    query: Optional[str] = None,
    include_content: bool = False,
    view_option: Optional[str] = None,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    directory_id: Optional[str] = None,
    page: Optional[int] = 1,
    limit: Optional[int] = None,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id)
    if knowledge is None:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.NOT_FOUND)
    if not _can_read_knowledge(knowledge, user):
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    data = _knowledge_data(knowledge)
    file_ids = data.get("file_ids", [])
    if not isinstance(file_ids, list):
        file_ids = []
    directories = _directory_map(knowledge)
    if directory_id and directory_id not in directories:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    file_directories = _file_directories(knowledge)
    needle = (query or "").casefold().strip()
    files = []
    for file in Files.get_files_by_ids(file_ids):
        searchable = file.filename
        if include_content and isinstance(file.data, dict):
            searchable += " " + str(file.data.get("content", ""))
        if needle and needle not in searchable.casefold():
            continue
        if directory_id is not None and file_directories.get(file.id) != (
            directory_id or None
        ):
            continue
        files.append(file)
    reverse = (direction or "desc").casefold() != "asc"
    key_name = (
        order_by
        if order_by in ("filename", "created_at", "updated_at")
        else "updated_at"
    )
    files.sort(
        key=lambda item: (getattr(item, key_name) or 0, item.id), reverse=reverse
    )
    total = len(files)
    page_size = min(200, max(1, limit or 30)) if user.role == "admin" else 30
    current_page = max(1, page or 1)
    files = files[(current_page - 1) * page_size : current_page * page_size]
    children = [
        value
        for value in directories.values()
        if value.parent_id == (directory_id or None)
    ]
    children.sort(key=lambda value: (value.name.casefold(), value.id))
    return KnowledgeFileListResponse(
        items=[
            _file_response(file, knowledge, directories, file_directories)
            for file in files
        ],
        total=total,
        directories=children,
        breadcrumbs=_directory_breadcrumbs(directory_id, directories),
    )


@router.get("/{id}/files/pending")
async def get_pending_knowledge_files(
    id: str, stream: bool = False, user=Depends(get_verified_user)
):
    knowledge = Knowledges.get_knowledge_by_id(id)
    if knowledge is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    if not _can_read_knowledge(knowledge, user):
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    file_ids = _knowledge_data(knowledge).get("file_ids", [])
    pending = [
        _file_response(file, knowledge).model_dump()
        for file in Files.get_files_by_ids(
            file_ids if isinstance(file_ids, list) else []
        )
        if isinstance(file.data, dict) and file.data.get("status") == "pending"
    ]
    if stream:

        async def events():
            yield f"data: {json.dumps(pending)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")
    return pending


def _archive_chunks(archive) -> Iterator[bytes]:
    try:
        while True:
            chunk = archive.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        archive.close()


@router.get("/{id}/export")
async def export_knowledge_by_id(id: str, user=Depends(get_verified_user)):
    if user.role != "admin":
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.UNAUTHORIZED)
    knowledge = Knowledges.get_knowledge_by_id(id)
    if knowledge is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    data = _knowledge_data(knowledge)
    file_ids = data.get("file_ids", [])
    directories = _directory_map(knowledge)
    file_directories = _file_directories(knowledge)
    archive = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for file in Files.get_files_by_ids(
            file_ids if isinstance(file_ids, list) else []
        ):
            if not file.path:
                continue
            try:
                source_path = Storage.get_file(file.path)
                directory_path = _directory_path(
                    file_directories.get(file.id), directories
                )
                archive_name = "/".join(
                    part
                    for part in (directory_path, os.path.basename(file.filename))
                    if part
                )
                output.write(source_path, arcname=archive_name)
            except Exception as error:
                log.warning(
                    "Skipping unavailable knowledge export file %s: %s", file.id, error
                )
    archive.seek(0)
    return StreamingResponse(
        _archive_chunks(archive),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="knowledge-{id}.zip"'},
    )


@router.post("/{id}/dirs/create", response_model=KnowledgeDirectoryModel)
async def create_knowledge_directory(
    id: str,
    form_data: KnowledgeDirectoryCreateForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id)
    if knowledge is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    if not _can_write_knowledge(knowledge, user):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    directories = _directory_models(knowledge)
    directory_map = {directory.id: directory for directory in directories}
    if form_data.parent_id and form_data.parent_id not in directory_map:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    now = int(time.time())
    directory = KnowledgeDirectoryModel(
        id=str(uuid.uuid4()),
        knowledge_id=id,
        name=_validate_directory_name(form_data.name),
        user_id=user.id,
        parent_id=form_data.parent_id,
        created_at=now,
        updated_at=now,
    )
    directories.append(directory)
    _save_knowledge_organization(knowledge, directories, _file_directories(knowledge))
    return directory


@router.post("/{id}/dirs/{dir_id}/update", response_model=KnowledgeDirectoryModel)
async def update_knowledge_directory(
    id: str,
    dir_id: str,
    form_data: KnowledgeDirectoryUpdateForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id)
    if knowledge is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    if not _can_write_knowledge(knowledge, user):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    directories = _directory_models(knowledge)
    directory_map = {directory.id: directory for directory in directories}
    directory = directory_map.get(dir_id)
    if directory is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    parent_id = directory.parent_id
    if form_data.parent_id != "__unset__":
        parent_id = form_data.parent_id
        if parent_id and parent_id not in directory_map:
            raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
        current = parent_id
        while current:
            if current == dir_id:
                raise HTTPException(
                    status_code=400, detail="Directory cycle is not allowed"
                )
            parent = directory_map.get(current)
            current = parent.parent_id if parent else None
    updated = directory.model_copy(
        update={
            "name": (
                _validate_directory_name(form_data.name)
                if form_data.name is not None
                else directory.name
            ),
            "parent_id": parent_id,
            "updated_at": int(time.time()),
        }
    )
    directories = [updated if value.id == dir_id else value for value in directories]
    _save_knowledge_organization(knowledge, directories, _file_directories(knowledge))
    return updated


@router.post("/{id}/file/move")
async def move_file_in_knowledge(
    id: str,
    form_data: KnowledgeFileMoveForm,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id)
    if knowledge is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    if not _can_write_knowledge(knowledge, user):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    data = _knowledge_data(knowledge)
    file_ids = data.get("file_ids", [])
    if (
        form_data.file_id not in file_ids
        or Files.get_file_by_id(form_data.file_id) is None
    ):
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    directories = _directory_models(knowledge)
    if form_data.directory_id and form_data.directory_id not in {
        directory.id for directory in directories
    }:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    file_directories = _file_directories(knowledge)
    file_directories[form_data.file_id] = form_data.directory_id
    _save_knowledge_organization(knowledge, directories, file_directories)
    return {"status": True}


@router.delete("/{id}/dirs/{dir_id}/delete")
async def delete_knowledge_directory(
    id: str,
    dir_id: str,
    move_files: bool = True,
    user=Depends(get_verified_user),
):
    knowledge = Knowledges.get_knowledge_by_id(id)
    if knowledge is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    if not _can_write_knowledge(knowledge, user):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    directories = _directory_models(knowledge)
    directory_map = {directory.id: directory for directory in directories}
    directory = directory_map.get(dir_id)
    if directory is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    children = {dir_id}
    changed = True
    while changed:
        before = len(children)
        children.update(
            value.id for value in directories if value.parent_id in children
        )
        changed = len(children) != before
    file_directories = _file_directories(knowledge)
    data = _knowledge_data(knowledge)
    file_ids = list(data.get("file_ids", []))
    if move_files:
        for file_id, directory_id in list(file_directories.items()):
            if directory_id in children:
                file_directories[file_id] = directory.parent_id
        for value in directories:
            if value.parent_id in children and value.id not in children:
                value.parent_id = directory.parent_id
    else:
        for file_id, directory_id in list(file_directories.items()):
            if directory_id not in children:
                continue
            file = Files.get_file_by_id(file_id)
            if file and file.path:
                try:
                    Storage.delete_file(file.path)
                except Exception as error:
                    log.warning(
                        "Unable to delete knowledge file %s: %s", file_id, error
                    )
            Files.delete_file_by_id(file_id)
            file_directories.pop(file_id, None)
            if file_id in file_ids:
                file_ids.remove(file_id)
    data["file_ids"] = file_ids
    data["directories"] = [
        value.model_dump() for value in directories if value.id not in children
    ]
    data["file_directories"] = file_directories
    Knowledges.update_knowledge_data_by_id(id, data)
    return {"status": True}


@router.post("/{id}/sync/diff", response_model=SyncDiffResponse)
async def sync_knowledge_diff(
    id: str, form_data: SyncDiffForm, user=Depends(get_verified_user)
):
    knowledge = Knowledges.get_knowledge_by_id(id)
    if knowledge is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    if not _can_write_knowledge(knowledge, user):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    directories = _directory_map(knowledge)
    file_directories = _file_directories(knowledge)
    file_ids = _knowledge_data(knowledge).get("file_ids", [])
    existing = {}
    for file in Files.get_files_by_ids(file_ids if isinstance(file_ids, list) else []):
        path = "/".join(
            part
            for part in (
                _directory_path(file_directories.get(file.id), directories),
                file.filename,
            )
            if part
        )
        existing[path] = file
    manifest = {}
    for entry in form_data.manifest:
        path = _validate_manifest_path(entry.path)
        if path in manifest:
            raise HTTPException(status_code=400, detail="Duplicate manifest path")
        manifest[path] = entry
    added = []
    modified = []
    unmodified_count = 0
    for path, entry in manifest.items():
        file = existing.get(path)
        if file is None:
            added.append({"filename": entry.filename, "path": path})
            continue
        expected_hash = (file.meta or {}).get("file_hash") or file.hash
        expected_size = (file.meta or {}).get("size")
        if expected_hash == entry.checksum and expected_size == entry.size:
            unmodified_count += 1
        else:
            modified.append(
                {"file_id": file.id, "filename": file.filename, "path": path}
            )
    deleted = [
        {"file_id": file.id, "filename": file.filename}
        for path, file in existing.items()
        if path not in manifest
    ]
    wanted_directories = {
        str(PurePosixPath(path).parent)
        for path in manifest
        if str(PurePosixPath(path).parent) != "."
    }
    directory_paths = {
        _directory_path(directory.id, directories): directory.id
        for directory in directories.values()
    }
    return SyncDiffResponse(
        added=added,
        modified=modified,
        deleted=deleted,
        mkdir=sorted(wanted_directories - set(directory_paths)),
        rmdir=sorted(
            directory_id
            for path, directory_id in directory_paths.items()
            if path not in wanted_directories
        ),
        unmodified_count=unmodified_count,
        directory_map=directory_paths,
    )


@router.post("/{id}/sync/cleanup")
async def sync_knowledge_cleanup(
    id: str, form_data: SyncCleanupForm, user=Depends(get_verified_user)
):
    knowledge = Knowledges.get_knowledge_by_id(id)
    if knowledge is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    if not _can_write_knowledge(knowledge, user):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    data = _knowledge_data(knowledge)
    file_ids = list(data.get("file_ids", []))
    file_directories = _file_directories(knowledge)
    for file_id in form_data.file_ids:
        if file_id not in file_ids:
            continue
        file = Files.get_file_by_id(file_id)
        if file and file.path:
            try:
                Storage.delete_file(file.path)
            except Exception as error:
                log.warning(
                    "Unable to remove stale knowledge file %s: %s", file_id, error
                )
        Files.delete_file_by_id(file_id)
        file_ids.remove(file_id)
        file_directories.pop(file_id, None)
    directories = _directory_models(knowledge)
    remove_ids = set(form_data.dir_ids)
    if remove_ids:
        changed = True
        while changed:
            before = len(remove_ids)
            remove_ids.update(
                directory.id
                for directory in directories
                if directory.parent_id in remove_ids
            )
            changed = len(remove_ids) != before
        directories = [
            directory for directory in directories if directory.id not in remove_ids
        ]
        for file_id, directory_id in list(file_directories.items()):
            if directory_id in remove_ids:
                file_directories[file_id] = None
    data["file_ids"] = file_ids
    data["directories"] = [directory.model_dump() for directory in directories]
    data["file_directories"] = file_directories
    Knowledges.update_knowledge_data_by_id(id, data)
    return {"status": True}
