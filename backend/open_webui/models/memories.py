import time
import uuid
from typing import Annotated, Any, Literal, Optional

from open_webui.internal.db import Base, JSONField, get_db
from pydantic import BaseModel, ConfigDict, WithJsonSchema
from sqlalchemy import BigInteger, Column, String, Text

####################
# Memory DB Schema
####################

OpenMapping = Annotated[
    dict[str, Any],
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]


class Memory(Base):
    __tablename__ = "memory"

    id = Column(String, primary_key=True)
    user_id = Column(String)
    content = Column(Text)
    path = Column(Text, nullable=True)
    type = Column(String, nullable=False, default="context")
    meta = Column(JSONField, nullable=True)
    updated_at = Column(BigInteger)
    created_at = Column(BigInteger)


class MemoryModel(BaseModel):
    """Pydantic mirror of the Memory table row."""

    id: str
    user_id: str
    content: str
    path: Optional[str] = None
    type: Literal["user", "context"] = "context"
    meta: Optional[OpenMapping] = None
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


####################
# Forms
####################


class MemoriesTable:
    def insert_new_memory(
        self,
        user_id: str,
        content: str,
        path: Optional[str] = None,
        type: Literal["user", "context"] = "context",
        meta: Optional[OpenMapping] = None,
    ) -> Optional[MemoryModel]:
        with get_db() as db:
            id = str(uuid.uuid4())

            memory = MemoryModel(
                **{
                    "id": id,
                    "user_id": user_id,
                    "content": content,
                    "path": path,
                    "type": type,
                    "meta": meta,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                }
            )
            result = Memory(**memory.model_dump())
            db.add(result)
            db.commit()
            db.refresh(result)
            if result:
                return MemoryModel.model_validate(result)
            else:
                return None

    def update_memory_by_id_and_user_id(
        self,
        id: str,
        user_id: str,
        content: Optional[str],
        *,
        path: Optional[str] = None,
        update_path: bool = False,
        type: Optional[Literal["user", "context"]] = None,
    ) -> Optional[MemoryModel]:
        with get_db() as db:
            try:
                values = {"updated_at": int(time.time())}
                if content is not None:
                    values["content"] = content
                if update_path:
                    values["path"] = path
                if type is not None:
                    values["type"] = type
                updated = db.query(Memory).filter_by(id=id, user_id=user_id).update(values)
                db.commit()
                return self.get_memory_by_id_and_user_id(id, user_id) if updated else None
            except Exception:
                return None

    def get_memories(self) -> list[MemoryModel]:
        with get_db() as db:
            try:
                memories = db.query(Memory).all()
                return [MemoryModel.model_validate(memory) for memory in memories]
            except Exception:
                return None

    def get_memories_by_user_id(self, user_id: str) -> list[MemoryModel]:
        with get_db() as db:
            try:
                memories = db.query(Memory).filter_by(user_id=user_id).all()
                return [MemoryModel.model_validate(memory) for memory in memories]
            except Exception:
                return None

    def get_memory_by_id(self, id: str) -> Optional[MemoryModel]:
        with get_db() as db:
            try:
                memory = db.get(Memory, id)
                return MemoryModel.model_validate(memory)
            except Exception:
                return None

    def get_memory_by_id_and_user_id(
        self, id: str, user_id: str
    ) -> Optional[MemoryModel]:
        with get_db() as db:
            try:
                memory = db.query(Memory).filter_by(id=id, user_id=user_id).first()
                return MemoryModel.model_validate(memory) if memory else None
            except Exception:
                return None

    def delete_memory_by_id(self, id: str) -> bool:
        with get_db() as db:
            try:
                db.query(Memory).filter_by(id=id).delete()
                db.commit()

                return True

            except Exception:
                return False

    def delete_memories_by_user_id(self, user_id: str) -> bool:
        with get_db() as db:
            try:
                db.query(Memory).filter_by(user_id=user_id).delete()
                db.commit()

                return True
            except Exception:
                return False

    def delete_memory_by_id_and_user_id(self, id: str, user_id: str) -> bool:
        with get_db() as db:
            try:
                db.query(Memory).filter_by(id=id, user_id=user_id).delete()
                db.commit()

                return True
            except Exception:
                return False


Memories = MemoriesTable()
