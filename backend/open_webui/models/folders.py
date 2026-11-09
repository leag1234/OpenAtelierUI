import logging
import time
import uuid
from typing import Any, Optional

from open_webui.internal.db import Base, get_db
from open_webui.models.chats import Chats

from open_webui.env import SRC_LOG_LEVELS
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Column, Text, JSON, Boolean
from open_webui.utils.access_control import get_permissions


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


def folder_permission_for_grants(
    grants: list[dict[str, Any]], user_id: str, group_ids=()
) -> Optional[str]:
    """Return the strongest explicit permission for one user."""
    group_ids = set(group_ids)
    matched = set()
    for grant in grants or []:
        if not isinstance(grant, dict):
            continue
        if (
            grant.get("principal_type") == "user"
            and grant.get("principal_id") == user_id
        ):
            matched.add(grant.get("permission"))
        elif (
            grant.get("principal_type") == "group"
            and grant.get("principal_id") in group_ids
        ):
            matched.add(grant.get("permission"))
    if "write" in matched:
        return "write"
    if "read" in matched:
        return "read"
    return None


####################
# Folder DB Schema
####################


class Folder(Base):
    __tablename__ = "folder"
    id = Column(Text, primary_key=True)
    parent_id = Column(Text, nullable=True)
    user_id = Column(Text)
    name = Column(Text)
    items = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    data = Column(JSON, nullable=True)
    is_expanded = Column(Boolean, default=False)
    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class FolderShareState(Base):
    __tablename__ = "folder_share_state"
    folder_id = Column(Text, primary_key=True)
    access_grants = Column(JSON, nullable=False, default=list)
    read_state = Column(JSON, nullable=False, default=dict)
    updated_at = Column(BigInteger, nullable=False)


class FolderModel(BaseModel):
    id: str
    parent_id: Optional[str] = None
    user_id: str
    name: str
    items: Optional[dict] = None
    meta: Optional[dict] = None
    data: Optional[dict] = None
    is_expanded: bool = False
    created_at: int
    updated_at: int

    model_config = ConfigDict(from_attributes=True)


class FolderAccessGrantModel(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    principal_type: str
    principal_id: str
    permission: str
    created_at: int


class FolderResponse(FolderModel):
    access_grants: list[FolderAccessGrantModel] = Field(default_factory=list)
    write_access: Optional[bool] = None


####################
# Forms
####################


class FolderForm(BaseModel):
    name: str
    parent_id: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class FolderTable:
    @staticmethod
    def _normalize_access_grants(
        folder_id: str, grants: list[dict[str, Any]], now: int
    ) -> list[dict[str, Any]]:
        if not isinstance(grants, list):
            raise ValueError("access_grants must be a list")
        normalized = []
        seen = set()
        for raw in grants:
            if not isinstance(raw, dict):
                raise ValueError("invalid folder access grant")
            principal_type = raw.get("principal_type")
            principal_id = raw.get("principal_id")
            permission = raw.get("permission")
            if principal_type not in ("user", "group"):
                raise ValueError("invalid folder access principal")
            if (
                not isinstance(principal_id, str)
                or not principal_id
                or principal_id == "*"
            ):
                raise ValueError("invalid folder access principal")
            if permission not in ("read", "write"):
                raise ValueError("invalid folder access permission")
            key = (principal_type, principal_id, permission)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    "id": str(uuid.uuid4()),
                    "resource_type": "folder",
                    "resource_id": folder_id,
                    "principal_type": principal_type,
                    "principal_id": principal_id,
                    "permission": permission,
                    "created_at": now,
                }
            )
        return normalized

    @staticmethod
    def _permission_for_grants(
        grants: list[dict[str, Any]], user_id: str
    ) -> Optional[str]:
        from open_webui.models.groups import Groups

        group_ids = {group.id for group in Groups.get_groups_by_member_id(user_id)}
        return folder_permission_for_grants(grants, user_id, group_ids)

    def insert_new_folder(
        self, user_id: str, name: str, parent_id: Optional[str] = None
    ) -> Optional[FolderModel]:
        with get_db() as db:
            id = str(uuid.uuid4())
            folder = FolderModel(
                **{
                    "id": id,
                    "user_id": user_id,
                    "name": name,
                    "parent_id": parent_id,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                }
            )
            try:
                result = Folder(**folder.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                if result:
                    return FolderModel.model_validate(result)
                else:
                    return None
            except Exception as e:
                log.exception(f"Error inserting a new folder: {e}")
                return None

    def get_folder_by_id_and_user_id(
        self, id: str, user_id: str
    ) -> Optional[FolderModel]:
        try:
            with get_db() as db:
                folder = db.query(Folder).filter_by(id=id, user_id=user_id).first()

                if not folder:
                    return None

                return FolderModel.model_validate(folder)
        except Exception:
            return None

    def get_folder_by_id(self, id: str) -> Optional[FolderModel]:
        try:
            with get_db() as db:
                folder = db.query(Folder).filter_by(id=id).first()
                return FolderModel.model_validate(folder) if folder else None
        except Exception:
            return None

    def get_access_grants(self, folder_id: str) -> list[dict[str, Any]]:
        with get_db() as db:
            state = db.query(FolderShareState).filter_by(folder_id=folder_id).first()
            return list(state.access_grants or []) if state else []

    def replace_access_grants(
        self, folder_id: str, grants: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        normalized = self._normalize_access_grants(folder_id, grants, now)
        with get_db() as db:
            state = (
                db.query(FolderShareState)
                .filter_by(folder_id=folder_id)
                .with_for_update()
                .first()
            )
            if state is None:
                state = FolderShareState(
                    folder_id=folder_id,
                    access_grants=normalized,
                    read_state={},
                    updated_at=now,
                )
                db.add(state)
            else:
                state.access_grants = normalized
                state.updated_at = now
            db.commit()
        return normalized

    def permission_for_user(self, folder_id: str, user_id: str) -> Optional[str]:
        return self._permission_for_grants(self.get_access_grants(folder_id), user_id)

    def get_shared_folders_by_user_id(
        self, user_id: str, include_owned: bool = True
    ) -> list[tuple[FolderModel, str]]:
        from open_webui.models.groups import Groups

        group_ids = {group.id for group in Groups.get_groups_by_member_id(user_id)}
        with get_db() as db:
            rows = (
                db.query(Folder, FolderShareState)
                .join(FolderShareState, Folder.id == FolderShareState.folder_id)
                .all()
            )
            result = []
            for folder, state in rows:
                grants = list(state.access_grants or [])
                if not grants:
                    continue
                if folder.user_id == user_id:
                    if include_owned:
                        result.append((FolderModel.model_validate(folder), "write"))
                    continue
                permission = folder_permission_for_grants(grants, user_id, group_ids)
                if permission:
                    result.append((FolderModel.model_validate(folder), permission))
            return result

    def mark_folder_read(self, folder_id: str, user_id: str, read_at: int) -> None:
        with get_db() as db:
            state = (
                db.query(FolderShareState)
                .filter_by(folder_id=folder_id)
                .with_for_update()
                .first()
            )
            if state is None:
                state = FolderShareState(
                    folder_id=folder_id,
                    access_grants=[],
                    read_state={user_id: read_at},
                    updated_at=read_at,
                )
                db.add(state)
            else:
                read_state = dict(state.read_state or {})
                read_state[user_id] = read_at
                state.read_state = read_state
                state.updated_at = read_at
            db.commit()

    def get_folder_read_at(self, folder_id: str, user_id: str) -> Optional[int]:
        with get_db() as db:
            state = db.query(FolderShareState).filter_by(folder_id=folder_id).first()
            value = (state.read_state or {}).get(user_id) if state else None
            return value if type(value) is int and value >= 0 else None

    def get_children_folders_by_id_and_user_id(
        self, id: str, user_id: str
    ) -> Optional[FolderModel]:
        try:
            with get_db() as db:
                folders = []

                def get_children(folder):
                    children = self.get_folders_by_parent_id_and_user_id(
                        folder.id, user_id
                    )
                    for child in children:
                        get_children(child)
                        folders.append(child)

                folder = db.query(Folder).filter_by(id=id, user_id=user_id).first()
                if not folder:
                    return None

                get_children(folder)
                return folders
        except Exception:
            return None

    def get_folders_by_user_id(self, user_id: str) -> list[FolderModel]:
        with get_db() as db:
            return [
                FolderModel.model_validate(folder)
                for folder in db.query(Folder).filter_by(user_id=user_id).all()
            ]

    def get_folder_by_parent_id_and_user_id_and_name(
        self, parent_id: Optional[str], user_id: str, name: str
    ) -> Optional[FolderModel]:
        try:
            with get_db() as db:
                # Check if folder exists
                folder = (
                    db.query(Folder)
                    .filter_by(parent_id=parent_id, user_id=user_id)
                    .filter(Folder.name.ilike(name))
                    .first()
                )

                if not folder:
                    return None

                return FolderModel.model_validate(folder)
        except Exception as e:
            log.error(f"get_folder_by_parent_id_and_user_id_and_name: {e}")
            return None

    def get_folders_by_parent_id_and_user_id(
        self, parent_id: Optional[str], user_id: str
    ) -> list[FolderModel]:
        with get_db() as db:
            return [
                FolderModel.model_validate(folder)
                for folder in db.query(Folder)
                .filter_by(parent_id=parent_id, user_id=user_id)
                .all()
            ]

    def update_folder_parent_id_by_id_and_user_id(
        self,
        id: str,
        user_id: str,
        parent_id: str,
    ) -> Optional[FolderModel]:
        try:
            with get_db() as db:
                folder = db.query(Folder).filter_by(id=id, user_id=user_id).first()

                if not folder:
                    return None

                folder.parent_id = parent_id
                folder.updated_at = int(time.time())

                db.commit()

                return FolderModel.model_validate(folder)
        except Exception as e:
            log.error(f"update_folder: {e}")
            return

    def update_folder_name_by_id_and_user_id(
        self, id: str, user_id: str, name: str
    ) -> Optional[FolderModel]:
        try:
            with get_db() as db:
                folder = db.query(Folder).filter_by(id=id, user_id=user_id).first()

                if not folder:
                    return None

                existing_folder = (
                    db.query(Folder)
                    .filter_by(name=name, parent_id=folder.parent_id, user_id=user_id)
                    .first()
                )

                if existing_folder:
                    return None

                folder.name = name
                folder.updated_at = int(time.time())

                db.commit()

                return FolderModel.model_validate(folder)
        except Exception as e:
            log.error(f"update_folder: {e}")
            return

    def update_folder_is_expanded_by_id_and_user_id(
        self, id: str, user_id: str, is_expanded: bool
    ) -> Optional[FolderModel]:
        try:
            with get_db() as db:
                folder = db.query(Folder).filter_by(id=id, user_id=user_id).first()

                if not folder:
                    return None

                folder.is_expanded = is_expanded
                folder.updated_at = int(time.time())

                db.commit()

                return FolderModel.model_validate(folder)
        except Exception as e:
            log.error(f"update_folder: {e}")
            return

    def delete_folder_by_id_and_user_id(
        self, id: str, user_id: str, delete_chats=True
    ) -> bool:
        try:
            with get_db() as db:
                folder = db.query(Folder).filter_by(id=id, user_id=user_id).first()
                if not folder:
                    return False

                if delete_chats:
                    # Delete all chats in the folder
                    Chats.delete_chats_by_user_id_and_folder_id(user_id, folder.id)

                # Delete all children folders
                def delete_children(folder):
                    folder_children = self.get_folders_by_parent_id_and_user_id(
                        folder.id, user_id
                    )
                    for folder_child in folder_children:
                        if delete_chats:
                            Chats.delete_chats_by_user_id_and_folder_id(
                                user_id, folder_child.id
                            )

                        delete_children(folder_child)

                        folder = db.query(Folder).filter_by(id=folder_child.id).first()
                        db.query(FolderShareState).filter_by(
                            folder_id=folder_child.id
                        ).delete()
                        db.delete(folder)
                        db.commit()

                delete_children(folder)
                db.query(FolderShareState).filter_by(folder_id=folder.id).delete()
                db.delete(folder)
                db.commit()
                return True
        except Exception as e:
            log.error(f"delete_folder: {e}")
            return False


Folders = FolderTable()
