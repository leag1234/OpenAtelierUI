import json
import logging
import time
from typing import Annotated, Any, Optional
import uuid

from open_webui.internal.db import Base, get_db
from open_webui.env import SRC_LOG_LEVELS

from open_webui.models.files import FileMetadataResponse
from open_webui.models.skills import AccessGrantModel
from open_webui.models.users import Users, UserResponse


from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    WithJsonSchema,
    computed_field,
    model_validator,
)
from sqlalchemy import BigInteger, Column, String, Text, JSON

from open_webui.utils.access_control import has_access
from open_webui.utils.access_grants import (
    knowledge_access_control_for_grants,
    knowledge_access_grants_for_acl,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

OpenMapping = Annotated[
    dict[str, Any],
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]

####################
# Knowledge DB Schema
####################


class Knowledge(Base):
    __tablename__ = "knowledge"

    id = Column(Text, unique=True, primary_key=True)
    user_id = Column(Text)

    name = Column(Text)
    description = Column(Text)

    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)

    access_control = Column(JSON, nullable=True)  # Controls data access levels.
    # Defines access control rules for this entry.
    # - `None`: Public access, available to all users with the "user" role.
    # - `{}`: Private access, restricted exclusively to the owner.
    # - Custom permissions: Specific access control for reading and writing;
    #   Can specify group or user-level restrictions:
    #   {
    #      "read": {
    #          "group_ids": ["group_id1", "group_id2"],
    #          "user_ids":  ["user_id1", "user_id2"]
    #      },
    #      "write": {
    #          "group_ids": ["group_id1", "group_id2"],
    #          "user_ids":  ["user_id1", "user_id2"]
    #      }
    #   }

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str

    name: str
    description: str

    data: Optional[dict] = None
    meta: Optional[OpenMapping] = None

    access_control: Optional[dict] = None

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch


####################
# Forms
####################


class KnowledgeUserModel(KnowledgeModel):
    user: Optional[UserResponse] = None


class KnowledgeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    description: str
    meta: Optional[OpenMapping] = None
    created_at: int
    updated_at: int
    files: Optional[list[FileMetadataResponse | OpenMapping]] = None
    access_grants: list[AccessGrantModel] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def project_public_fields(cls, data):
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        if not isinstance(data, dict):
            return data
        value = dict(data)
        if "access_grants" not in value:
            value["access_grants"] = knowledge_access_grants_for_acl(
                value.get("access_control"),
                "knowledge",
                value.get("id", ""),
                value.get("created_at", 0),
            )
        value.pop("access_control", None)
        value.pop("data", None)
        return value


class KnowledgeUserResponse(KnowledgeUserModel):
    files: Optional[list[FileMetadataResponse | dict]] = None

    @computed_field
    @property
    def access_grants(self) -> list[dict]:
        return knowledge_access_grants_for_acl(
            self.access_control, "knowledge", self.id, self.created_at
        )


class KnowledgeForm(BaseModel):
    name: str
    description: str
    data: Optional[dict] = None
    access_control: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def convert_access_grants(cls, data):
        if isinstance(data, dict) and "access_grants" in data:
            converted = dict(data)
            converted.pop("access_control", None)
            converted["access_control"] = knowledge_access_control_for_grants(
                converted.pop("access_grants")
            )
            return converted
        return data


class KnowledgeAccessResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str
    meta: Optional[dict] = None
    user: Optional[UserResponse] = None
    created_at: int
    updated_at: int
    file_count: Optional[int] = None
    write_access: Optional[bool] = False
    access_grants: list[AccessGrantModel] = Field(default_factory=list)


class KnowledgeAccessListResponse(BaseModel):
    items: list[KnowledgeAccessResponse]
    total: int


class KnowledgeDirectoryCreateForm(BaseModel):
    name: str
    parent_id: Optional[str] = None


class KnowledgeDirectoryUpdateForm(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = "__unset__"


class KnowledgeDirectoryModel(BaseModel):
    id: str
    knowledge_id: str
    name: str
    user_id: str
    parent_id: Optional[str] = None
    created_at: int
    updated_at: int


class FileManifestEntry(BaseModel):
    filename: str
    path: str
    checksum: str
    size: int


class KnowledgeFileMoveForm(BaseModel):
    file_id: str
    directory_id: Optional[str] = None


class SyncCleanupForm(BaseModel):
    file_ids: list[str]
    dir_ids: list[str] = Field(default_factory=list)


class SyncDiffForm(BaseModel):
    manifest: list[FileManifestEntry]


class SyncDiffResponse(BaseModel):
    added: list[dict]
    modified: list[dict]
    deleted: list[dict]
    mkdir: list[str]
    rmdir: list[str]
    unmodified_count: int
    directory_map: dict[str, str]


class KnowledgeTable:
    def insert_new_knowledge(
        self, user_id: str, form_data: KnowledgeForm
    ) -> Optional[KnowledgeModel]:
        with get_db() as db:
            knowledge = KnowledgeModel(
                **{
                    **form_data.model_dump(),
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                }
            )

            try:
                result = Knowledge(**knowledge.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                if result:
                    return KnowledgeModel.model_validate(result)
                else:
                    return None
            except Exception:
                return None

    def get_knowledge_bases(self) -> list[KnowledgeUserModel]:
        with get_db() as db:
            knowledge_bases = []
            for knowledge in (
                db.query(Knowledge).order_by(Knowledge.updated_at.desc()).all()
            ):
                user = Users.get_user_by_id(knowledge.user_id)
                knowledge_bases.append(
                    KnowledgeUserModel.model_validate(
                        {
                            **KnowledgeModel.model_validate(knowledge).model_dump(),
                            "user": user.model_dump() if user else None,
                        }
                    )
                )
            return knowledge_bases

    def get_knowledge_bases_by_user_id(
        self, user_id: str, permission: str = "write"
    ) -> list[KnowledgeUserModel]:
        knowledge_bases = self.get_knowledge_bases()
        return [
            knowledge_base
            for knowledge_base in knowledge_bases
            if knowledge_base.user_id == user_id
            or has_access(user_id, permission, knowledge_base.access_control)
        ]

    def get_knowledge_by_id(self, id: str) -> Optional[KnowledgeModel]:
        try:
            with get_db() as db:
                knowledge = db.query(Knowledge).filter_by(id=id).first()
                return KnowledgeModel.model_validate(knowledge) if knowledge else None
        except Exception:
            return None

    def update_knowledge_by_id(
        self, id: str, form_data: KnowledgeForm, overwrite: bool = False
    ) -> Optional[KnowledgeModel]:
        try:
            with get_db() as db:
                knowledge = self.get_knowledge_by_id(id=id)
                if knowledge is None:
                    return None
                values = form_data.model_dump()
                if not overwrite:
                    values["access_control"] = knowledge.access_control
                db.query(Knowledge).filter_by(id=id).update(
                    {
                        **values,
                        "updated_at": int(time.time()),
                    }
                )
                db.commit()
                return self.get_knowledge_by_id(id=id)
        except Exception as e:
            log.exception(e)
            return None

    def update_knowledge_data_by_id(
        self, id: str, data: dict
    ) -> Optional[KnowledgeModel]:
        try:
            with get_db() as db:
                knowledge = self.get_knowledge_by_id(id=id)
                db.query(Knowledge).filter_by(id=id).update(
                    {
                        "data": data,
                        "updated_at": int(time.time()),
                    }
                )
                db.commit()
                return self.get_knowledge_by_id(id=id)
        except Exception as e:
            log.exception(e)
            return None

    def update_knowledge_meta_by_id(
        self, id: str, meta: dict
    ) -> Optional[KnowledgeModel]:
        try:
            with get_db() as db:
                updated = (
                    db.query(Knowledge)
                    .filter_by(id=id)
                    .update({"meta": meta, "updated_at": int(time.time())})
                )
                db.commit()
                return self.get_knowledge_by_id(id=id) if updated else None
        except Exception as e:
            log.exception(e)
            return None

    def update_knowledge_access_control_by_id(
        self, id: str, access_control: Optional[dict]
    ) -> Optional[KnowledgeModel]:
        try:
            with get_db() as db:
                updated = (
                    db.query(Knowledge)
                    .filter_by(id=id)
                    .update(
                        {
                            "access_control": access_control,
                            "updated_at": int(time.time()),
                        }
                    )
                )
                if updated != 1:
                    db.rollback()
                    return None
                db.commit()
                return self.get_knowledge_by_id(id=id)
        except Exception as e:
            log.exception(e)
            return None

    def delete_knowledge_by_id(self, id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Knowledge).filter_by(id=id).delete()
                db.commit()
                return True
        except Exception:
            return False

    def delete_all_knowledge(self) -> bool:
        with get_db() as db:
            try:
                db.query(Knowledge).delete()
                db.commit()

                return True
            except Exception:
                return False


Knowledges = KnowledgeTable()
