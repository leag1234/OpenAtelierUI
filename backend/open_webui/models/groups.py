import json
import logging
import time
from typing import Literal, Optional
import uuid

from open_webui.internal.db import Base, get_db
from open_webui.env import SRC_LOG_LEVELS

from open_webui.models.files import FileMetadataResponse


from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Text, JSON, func, text


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

####################
# UserGroup DB Schema
####################


class Group(Base):
    __tablename__ = "group"

    id = Column(Text, unique=True, primary_key=True)
    user_id = Column(Text)

    name = Column(Text)
    description = Column(Text)

    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)

    permissions = Column(JSON, nullable=True)
    user_ids = Column(JSON, nullable=True)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class GroupModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str

    name: str
    description: str

    data: Optional[dict] = None
    meta: Optional[dict] = None

    permissions: Optional[dict] = None
    user_ids: list[str] = []

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch


####################
# Forms
####################


class GroupResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str
    permissions: Optional[dict] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    user_ids: list[str] = []
    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch


class CurrentGroupResponse(BaseModel):
    """Measured flat-group response; membership is exposed on its own route."""

    id: str
    user_id: str
    name: str
    description: str
    permissions: Optional[dict] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    parent_group_id: Optional[str] = None
    member_count: int
    created_at: int
    updated_at: int


class GroupMemberResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    membership_type: str
    is_active: bool
    last_active_at: Optional[int] = None
    bio: Optional[str] = None
    timezone: Optional[str] = None
    status_emoji: Optional[str] = None
    status_message: Optional[str] = None
    status_expires_at: Optional[int] = None
    groups: list = []
    via_group_ids: list = []


class GroupMembersResponse(BaseModel):
    counts: dict[str, int]
    items: list[GroupMemberResponse]
    total: int


class GroupUserIdsForm(BaseModel):
    user_ids: Optional[list[str]] = None


class GroupForm(BaseModel):
    name: str
    description: str
    permissions: Optional[dict] = None


class GroupUpdateForm(GroupForm):
    user_ids: Optional[list[str]] = None


class GroupTable:
    def get_group_members_snapshot(self, id: str):
        """Return one group and its directly listed users from one read session."""
        from open_webui.models.users import User

        with get_db() as db:
            group = db.query(Group).filter_by(id=id).first()
            if group is None:
                return None, []
            raw_ids = group.user_ids
            if not isinstance(raw_ids, list) or any(not isinstance(item, str) for item in raw_ids):
                raise ValueError("Group membership storage is not a string array")
            ids = list(dict.fromkeys(raw_ids))
            users = db.query(User).filter(User.id.in_(ids)).all() if ids else []
            return GroupModel.model_validate(group), users

    def mutate_group_user_ids(self, id: str, user_ids: list[str], action: Literal["add", "remove"]):
        """Change direct flat membership atomically for the measured SQLite store."""
        if action not in ("add", "remove"):
            raise ValueError("Unsupported group membership action")
        from open_webui.models.users import User

        db_context = get_db()
        with db_context as db:
            try:
                if db.bind.dialect.name == "sqlite":
                    # This must be the first database statement in the session.
                    db.execute(text("BEGIN IMMEDIATE"))
                    group = db.query(Group).filter_by(id=id).first()
                else:
                    group = db.query(Group).filter_by(id=id).with_for_update().first()
                if group is None:
                    return None
                current = group.user_ids
                if not isinstance(current, list) or any(not isinstance(item, str) for item in current):
                    raise ValueError("Group membership storage is not a string array")
                current = list(dict.fromkeys(current))
                valid_ids = set(
                    row[0]
                    for row in db.query(User.id).filter(User.id.in_(set(user_ids))).all()
                ) if user_ids else set()
                if action == "add":
                    requested_ids = list(dict.fromkeys(user_ids))
                    updated = current + [
                        item
                        for item in requested_ids
                        if item in valid_ids and item not in current
                    ]
                else:
                    remove = valid_ids
                    updated = [item for item in current if item not in remove]
                if updated == current:
                    return GroupModel.model_validate(group)
                group.user_ids = updated
                group.updated_at = int(time.time())
                db.commit()
                db.refresh(group)
                return GroupModel.model_validate(group)
            except Exception:
                db.rollback()
                raise

    def insert_new_group(
        self, user_id: str, form_data: GroupForm
    ) -> Optional[GroupModel]:
        with get_db() as db:
            group = GroupModel(
                **{
                    **form_data.model_dump(exclude_none=True),
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                }
            )

            try:
                result = Group(**group.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                if result:
                    return GroupModel.model_validate(result)
                else:
                    return None

            except Exception:
                return None

    def get_groups(self) -> list[GroupModel]:
        with get_db() as db:
            return [
                GroupModel.model_validate(group)
                for group in db.query(Group).order_by(Group.updated_at.desc()).all()
            ]

    def get_groups_by_member_id(self, user_id: str) -> list[GroupModel]:
        with get_db() as db:
            return [
                GroupModel.model_validate(group)
                for group in db.query(Group)
                .filter(func.json_array_length(Group.user_ids) > 0)
                .order_by(Group.updated_at.desc())
                .all()
                if isinstance(group.user_ids, list) and user_id in group.user_ids
            ]

    def get_group_by_id(self, id: str) -> Optional[GroupModel]:
        try:
            with get_db() as db:
                group = db.query(Group).filter_by(id=id).first()
                return GroupModel.model_validate(group) if group else None
        except Exception:
            return None

    def get_group_user_ids_by_id(self, id: str) -> Optional[str]:
        group = self.get_group_by_id(id)
        if group:
            return group.user_ids
        else:
            return None

    def update_group_by_id(
        self, id: str, form_data: GroupUpdateForm, overwrite: bool = False
    ) -> Optional[GroupModel]:
        try:
            with get_db() as db:
                db.query(Group).filter_by(id=id).update(
                    {
                        **form_data.model_dump(exclude_none=True),
                        "updated_at": int(time.time()),
                    }
                )
                db.commit()
                return self.get_group_by_id(id=id)
        except Exception as e:
            log.exception(e)
            return None

    def delete_group_by_id(self, id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Group).filter_by(id=id).delete()
                db.commit()
                return True
        except Exception:
            return False

    def delete_all_groups(self) -> bool:
        with get_db() as db:
            try:
                db.query(Group).delete()
                db.commit()

                return True
            except Exception:
                return False

    def remove_user_from_all_groups(self, user_id: str) -> bool:
        with get_db() as db:
            try:
                groups = self.get_groups_by_member_id(user_id)

                for group in groups:
                    group.user_ids.remove(user_id)
                    db.query(Group).filter_by(id=group.id).update(
                        {
                            "user_ids": group.user_ids,
                            "updated_at": int(time.time()),
                        }
                    )
                    db.commit()

                return True
            except Exception:
                return False


Groups = GroupTable()
