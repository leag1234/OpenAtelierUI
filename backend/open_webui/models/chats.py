import copy
import logging
import json
import time
import uuid
from typing import Optional

from open_webui.internal.db import Base, get_db
from open_webui.models.tags import TagModel, Tag, Tags
from open_webui.env import SRC_LOG_LEVELS

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator
from sqlalchemy import BigInteger, Boolean, Column, String, Text, JSON, cast, update
from sqlalchemy import or_, func, select, and_, text
from sqlalchemy.sql import exists

####################
# Chat DB Schema
####################

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

SAVED_WORK_META_KEY = "_mdk_private_saved_work"


def _saved_work_marker(meta):
    if not isinstance(meta, dict):
        return None
    marker = meta.get(SAVED_WORK_META_KEY)
    if not isinstance(marker, dict):
        return None
    if type(marker.get("version")) is not int or marker["version"] != 1:
        return None
    if set(marker) != {"version", "last_read_at"}:
        return None
    last_read_at = marker.get("last_read_at")
    if last_read_at is not None and (type(last_read_at) is not int or last_read_at < 0):
        return None
    return marker


def _new_saved_work_meta(meta=None, last_read_at=None):
    result = copy.deepcopy(meta) if isinstance(meta, dict) else {}
    if SAVED_WORK_META_KEY not in result:
        result[SAVED_WORK_META_KEY] = {"version": 1, "last_read_at": last_read_at}
    return result


def _public_chat_meta(meta):
    result = copy.deepcopy(meta) if isinstance(meta, dict) else {}
    if _saved_work_marker(result) is not None:
        result.pop(SAVED_WORK_META_KEY, None)
    return result


def _saved_work_last_read(meta):
    marker = _saved_work_marker(meta)
    return marker.get("last_read_at") if marker else None


def _updated_saved_work_read_meta(meta, read_at):
    if type(read_at) is not int or read_at < 0:
        return None
    marker = _saved_work_marker(meta)
    if marker is None:
        return None
    result = copy.deepcopy(meta)
    result[SAVED_WORK_META_KEY]["last_read_at"] = read_at
    return result


def _normalize_private_chat_authors(chat, user, user_lookup=None):
    result = copy.deepcopy(chat) if isinstance(chat, dict) else {}
    user_id = getattr(user, "id", None)
    profiles = {}

    def author_profile(author_id):
        if author_id not in profiles:
            if author_id == user_id:
                name = getattr(user, "name", None)
                if name is None and user_lookup is not None:
                    resolved = user_lookup(author_id)
                    name = getattr(resolved, "name", None) if resolved else None
                profiles[author_id] = {"id": author_id, "name": name}
            elif user_lookup is not None:
                resolved = user_lookup(author_id)
                profiles[author_id] = (
                    {"id": author_id, "name": getattr(resolved, "name", None)}
                    if resolved
                    else None
                )
            else:
                profiles[author_id] = None
        return profiles[author_id]

    message_maps = []
    history = result.get("history")
    if isinstance(history, dict) and isinstance(history.get("messages"), dict):
        message_maps.append(history["messages"])
    flat_messages = result.get("messages")
    if isinstance(flat_messages, list):
        message_maps.append(flat_messages)

    for collection in message_maps:
        messages = collection.values() if isinstance(collection, dict) else collection
        for message in messages:
            if not isinstance(message, dict):
                continue
            message["user_id"] = user_id
            if message.get("role") == "user":
                profile = author_profile(user_id)
                if profile is not None:
                    message["user"] = copy.deepcopy(profile)
                else:
                    message.pop("user", None)
            else:
                message.pop("user", None)
    return result


def _project_private_chat_authors(chat, user, user_lookup=None):
    result = copy.deepcopy(chat) if isinstance(chat, dict) else {}
    caller_id = getattr(user, "id", None)
    profile_cache = {}

    def profile_for(author_id):
        if author_id not in profile_cache:
            if author_id == caller_id:
                profile_cache[author_id] = {
                    "id": caller_id,
                    "name": getattr(user, "name", None),
                }
            elif user_lookup is not None:
                resolved = user_lookup(author_id)
                profile_cache[author_id] = (
                    {"id": getattr(resolved, "id", author_id), "name": getattr(resolved, "name", None)}
                    if resolved
                    else None
                )
            else:
                profile_cache[author_id] = None
        return profile_cache[author_id]

    collections = []
    history = result.get("history")
    if isinstance(history, dict) and isinstance(history.get("messages"), dict):
        collections.append(history["messages"])
    flat_messages = result.get("messages")
    if isinstance(flat_messages, list):
        collections.append(flat_messages)
    for collection in collections:
        messages = collection.values() if isinstance(collection, dict) else collection
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            author_id = message.get("user_id")
            if not isinstance(author_id, str) or not author_id:
                continue
            profile = profile_for(author_id)
            if profile is not None:
                message["user"] = copy.deepcopy(profile)
    return result


def _chat_current_message_id(chat):
    if not isinstance(chat, dict):
        return None
    history = chat.get("history")
    if not isinstance(history, dict):
        return None
    current_id = history.get("currentId")
    return current_id if isinstance(current_id, str) else None


class Chat(Base):
    __tablename__ = "chat"

    id = Column(String, primary_key=True)
    user_id = Column(String)
    title = Column(Text)
    chat = Column(JSON)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)

    share_id = Column(Text, unique=True, nullable=True)
    archived = Column(Boolean, default=False)
    pinned = Column(Boolean, default=False, nullable=True)

    meta = Column(JSON, server_default="{}")
    folder_id = Column(Text, nullable=True)


class ChatModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    chat: dict

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    share_id: Optional[str] = None
    archived: bool = False
    pinned: Optional[bool] = False

    meta: dict = {}
    folder_id: Optional[str] = None


####################
# Forms
####################


class ChatForm(BaseModel):
    chat: dict


class PrivateChatForm(ChatForm):
    variables: Optional[dict] = None
    folder_id: Optional[str] = None


class ChatImportForm(ChatForm):
    meta: Optional[dict] = {}
    pinned: Optional[bool] = False
    folder_id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def reject_bulk_envelope(cls, value):
        if isinstance(value, dict) and "chats" in value:
            raise ValueError("Use the bulk import form for chat arrays.")
        return value


class ChatImportItemForm(ChatImportForm):
    model_config = ConfigDict(extra="forbid")

    id: Optional[StrictStr] = None
    user_id: Optional[StrictStr] = None
    title: Optional[StrictStr] = None
    created_at: Optional[StrictInt] = None
    updated_at: Optional[StrictInt] = None
    current_message_id: Optional[StrictStr] = None
    variables: Optional[dict] = None
    archived: Optional[StrictBool] = None
    share_id: Optional[StrictStr] = None
    pinned: Optional[StrictBool] = False


class ChatsImportForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chats: list[ChatImportItemForm]


class ChatTitleMessagesForm(BaseModel):
    title: str
    messages: list[dict]


class ChatTitleForm(BaseModel):
    title: str


class ChatResponse(BaseModel):
    id: str
    user_id: str
    title: str
    chat: dict
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch
    share_id: Optional[str] = None  # id of the chat to be shared
    archived: bool
    pinned: Optional[bool] = False
    meta: dict = {}
    folder_id: Optional[str] = None


class PrivateChatResponse(ChatResponse):
    variables: dict = Field(default_factory=dict)
    tasks: Optional[list] = None
    summary: Optional[str] = None
    current_message_id: Optional[str] = None
    context_usage: Optional[dict] = None


class ChatTitleIdResponse(BaseModel):
    id: str
    title: str
    updated_at: int
    created_at: int


class PrivateChatTitleIdResponse(ChatTitleIdResponse):
    last_read_at: Optional[int] = None
    snippet: Optional[str] = None
    active: bool = False
    archived: bool = False


def _chat_text_file_reference(chat):
    unsupported = "Attached file import is unsupported."

    def refs(value):
        if value in (None, "", [], {}):
            return []
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise ValueError(unsupported)
        return value

    history = chat.get("history")
    nodes = history.get("messages") if isinstance(history, dict) else None
    flat = chat.get("messages")
    if not isinstance(nodes, dict) or not isinstance(flat, list):
        if refs(chat.get("files")):
            raise ValueError(unsupported)
        return None, set()
    top = refs(chat.get("files"))
    history_found = [(key, node, refs(node.get("files"))) for key, node in nodes.items() if isinstance(node, dict) and refs(node.get("files"))]
    flat_found = [(node, refs(node.get("files"))) for node in flat if isinstance(node, dict) and refs(node.get("files"))]
    if not top and not history_found and not flat_found:
        return None, set()
    if len(top) != 1 or len(history_found) != 1 or len(flat_found) != 1:
        raise ValueError(unsupported)
    history_id, history_node, history_ref = history_found[0]
    flat_node, flat_ref = flat_found[0]
    reference = top[0]
    if history_node.get("role") != "user" or flat_node.get("role") != "user" or history_node.get("id") != history_id or flat_node.get("id") != history_id:
        raise ValueError(unsupported)
    selected = history.get("currentId")
    if not isinstance(selected, str) or selected not in nodes:
        raise ValueError(unsupported)
    ancestry, visited, cursor = [], set(), selected
    while cursor is not None:
        if not isinstance(cursor, str) or cursor in visited or cursor not in nodes or not isinstance(nodes[cursor], dict):
            raise ValueError(unsupported)
        visited.add(cursor); ancestry.append(cursor)
        cursor = nodes[cursor].get("parentId")
    if history_id not in ancestry or [node.get("id") if isinstance(node, dict) else None for node in flat] != list(reversed(ancestry)):
        raise ValueError(unsupported)
    if history_ref[0] != reference or flat_ref[0] != reference:
        raise ValueError(unsupported)
    allowed = {(id(chat), "files"), (id(history_node), "files"), (id(flat_node), "files")}
    allowed.update((id(ref), "file") for ref in (reference, history_ref[0], flat_ref[0]))
    if not _valid_text_file_reference_shape(reference):
        raise ValueError(unsupported)
    return reference, allowed


def _valid_text_file_reference_shape(reference):
    item_keys = {"error", "file", "id", "itemId", "name", "size", "status", "type", "url"}
    file_keys = {"created_at", "data", "filename", "hash", "id", "meta", "updated_at", "user_id"}
    if set(reference) != item_keys:
        return False
    identifier = reference.get("id")
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in identifier):
        return False
    if reference.get("type") != "file" or reference.get("status") != "uploaded" or reference.get("error") != "":
        return False
    if reference.get("url") != "/api/v1/files/" + identifier:
        return False
    if not isinstance(reference.get("itemId"), str) or not 1 <= len(reference["itemId"]) <= 128:
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in reference["itemId"]):
        return False
    size = reference.get("size")
    file = reference.get("file")
    if type(size) is not int or not 0 < size <= 256 * 1024 or not isinstance(file, dict) or set(file) != file_keys:
        return False
    meta = file.get("meta")
    if not isinstance(meta, dict) or set(meta) not in ({"name", "content_type", "size", "data"}, {"name", "content_type", "size", "data", "file_hash"}):
        return False
    if (file.get("id") != identifier or not isinstance(file.get("user_id"), str) or
            not isinstance(file.get("filename"), str) or not 1 <= len(file["filename"]) <= 255 or
            file["filename"] in {".", ".."} or "/" in file["filename"] or "\\" in file["filename"] or "\x00" in file["filename"] or
            any(ord(c) < 32 or ord(c) == 127 for c in file["filename"]) or file.get("filename") != reference.get("name") or
            meta.get("name") != reference.get("name") or meta.get("content_type") != "text/plain" or
            type(meta.get("size")) is not int or
            meta.get("size") != size or file.get("data") != {} or meta.get("data") != {}):
        return False
    if file.get("hash") is not None:
        return False
    if "file_hash" in meta and not isinstance(meta["file_hash"], str):
        return False
    return type(file.get("created_at")) is int and type(file.get("updated_at")) is int


def _lookup_import_file(file_id):
    from open_webui.models.files import Files
    return Files.get_file_by_id(file_id)


def _validate_owned_text_file(reference, user_id, row):
    unsupported = "Attached file import is unsupported."
    try:
        if (row is None or getattr(row, "id", None) != reference["id"] or
                getattr(row, "user_id", None) != user_id or reference["file"].get("user_id") != user_id):
            raise ValueError(unsupported)
        if getattr(row, "access_control", None) not in (None, {}):
            raise ValueError(unsupported)
        if getattr(row, "data", None) != {}:
            raise ValueError(unsupported)
        file = reference["file"]
        meta = getattr(row, "meta", None)
        if not isinstance(meta, dict) or set(meta) not in ({"name", "content_type", "size", "data"}, {"name", "content_type", "size", "data", "file_hash"}):
            raise ValueError(unsupported)
        if type(meta.get("size")) is not int:
            raise ValueError(unsupported)
        if (getattr(row, "filename", None) != file["filename"] or getattr(row, "hash", None) != file["hash"] or
                getattr(row, "created_at", None) != file["created_at"] or getattr(row, "updated_at", None) != file["updated_at"] or
                getattr(row, "data", None) != file["data"] or meta != file["meta"]):
            raise ValueError(unsupported)
        if meta.get("content_type") != "text/plain" or meta.get("size") != reference["size"]:
            raise ValueError(unsupported)
    except Exception:
        raise ValueError(unsupported) from None


def _prepare_bulk_chat_import(chats):
    if not isinstance(chats, list) or not 1 <= len(chats) <= 16:
        raise ValueError("Bulk import requires 1 to 16 chats.")
    try:
        payload_size = len(json.dumps({"chats": chats}, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("Bulk import contains unsupported data.") from None
    if payload_size > 4 * 1024 * 1024:
        raise ValueError("Bulk import exceeds the 4 MiB limit.")
    prepared, node_count, batch_file_id = [], 0, None
    allowed = {"id", "user_id", "title", "chat", "meta", "pinned", "folder_id", "created_at", "updated_at", "archived", "share_id", "current_message_id", "variables"}
    for item in chats:
        if not isinstance(item, dict) or set(item) - allowed or not isinstance(item.get("chat"), dict):
            raise ValueError("Bulk import contains an unsupported chat shape.")
        if item.get("folder_id") is not None or item.get("share_id") is not None or item.get("archived", False) is not False:
            raise ValueError("Folders, shared chats, and archived imports are unsupported.")
        if ("meta" in item and item["meta"] is None) or item.get("meta", {}) != {}:
            raise ValueError("Unsupported chat metadata.")
        if item.get("variables") is not None:
            raise ValueError("Non-empty chat variables are unsupported.")
        if item.get("current_message_id") is not None and not isinstance(item["current_message_id"], str):
            raise ValueError("Invalid selected message.")
        if item.get("pinned", False) is None:
            item = {**item, "pinned": False}
        if type(item.get("pinned", False)) is not bool:
            raise ValueError("Invalid pinned value.")
        if "title" in item and not isinstance(item["title"], str):
            raise ValueError("Invalid chat title.")
        for field in ("created_at", "updated_at"):
            if field in item and item[field] is None:
                item = {key: value for key, value in item.items() if key != field}
            elif field in item and (type(item[field]) is not int or item[field] < 0):
                raise ValueError("Invalid chat timestamp.")
        graph = item["chat"]
        if not isinstance(graph.get("title", item.get("title", "New Chat")), str):
            raise ValueError("Invalid chat title.")
        history = graph.get("history")
        if "history" in graph and not isinstance(history, dict):
            raise ValueError("Invalid chat message graph.")
        messages = history.get("messages", {}) if isinstance(history, dict) else {}
        if isinstance(messages, dict):
            node_count += len(messages)
        elif isinstance(messages, list):
            node_count += len(messages)
        else:
            raise ValueError("Invalid chat message graph.")
        selected = item.get("current_message_id") if item.get("current_message_id") is not None else (history.get("currentId") if isinstance(history, dict) else None)
        if selected is not None and (not isinstance(selected, str) or not isinstance(messages, dict) or selected not in messages):
            raise ValueError("Selected message is missing from the chat graph.")
        if item.get("current_message_id") is not None:
            if not isinstance(graph.get("history", {}), dict):
                raise ValueError("Invalid chat message graph.")
            graph = copy.deepcopy(graph)
            graph.setdefault("history", {})["currentId"] = selected
            item = {**item, "chat": graph}
        file_reference, allowed_file_fields = _chat_text_file_reference(graph)
        if file_reference is not None:
            if batch_file_id is not None and batch_file_id != file_reference["id"]:
                raise ValueError("Attached file import is unsupported.")
            batch_file_id = file_reference["id"]
        stack = [graph]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if current.get("role") == "tool" or current.get("type") in ("image", "input_image"):
                    raise ValueError("Only text conversations are supported.")
                for key in ("file", "files", "attachments", "attachment", "file_ids", "images", "tool_calls", "tool_call", "tool_call_id", "function_call", "image_url"):
                    if key in current and current[key] not in (None, [], {}) and (id(current), key) not in allowed_file_fields:
                        raise ValueError("Media and tool content are unsupported.")
                if "content" in current and current["content"] not in (None, "") and not isinstance(current["content"], str):
                    raise ValueError("Only text conversations are supported.")
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
        prepared.append(item)
    if node_count > 4096:
        raise ValueError("Bulk import exceeds the 4096-message limit.")
    return prepared


class ChatTable:
    def insert_new_chat(
        self,
        user_id: str,
        form_data: ChatForm,
        compatibility_saved_work: bool = False,
    ) -> Optional[ChatModel]:
        with get_db() as db:
            id = str(uuid.uuid4())
            created_at = int(time.time())
            updated_at = (
                created_at if compatibility_saved_work else int(time.time())
            )
            values = {
                "id": id,
                "user_id": user_id,
                "title": (
                    form_data.chat["title"]
                    if "title" in form_data.chat
                    else "New Chat"
                ),
                "chat": form_data.chat,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            if compatibility_saved_work:
                values["meta"] = _new_saved_work_meta(last_read_at=created_at)
            chat = ChatModel(**values)

            result = Chat(**chat.model_dump())
            db.add(result)
            db.commit()
            db.refresh(result)
            return ChatModel.model_validate(result) if result else None

    def import_chat(
        self, user_id: str, form_data: ChatImportForm
    ) -> Optional[ChatModel]:
        with get_db() as db:
            id = str(uuid.uuid4())
            chat = ChatModel(
                **{
                    "id": id,
                    "user_id": user_id,
                    "title": (
                        form_data.chat["title"]
                        if "title" in form_data.chat
                        else "New Chat"
                    ),
                    "chat": form_data.chat,
                    "meta": form_data.meta,
                    "pinned": form_data.pinned,
                    "folder_id": form_data.folder_id,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                }
            )

            result = Chat(**chat.model_dump())
            db.add(result)
            db.commit()
            db.refresh(result)
            return ChatModel.model_validate(result) if result else None

    def import_chats(self, user_id: str, chats: list[dict]) -> list[ChatModel]:
        prepared = _prepare_bulk_chat_import(chats)
        file_references = [reference for item in prepared if (reference := _chat_text_file_reference(item["chat"])[0]) is not None]
        if file_references:
            try:
                row = _lookup_import_file(file_references[0]["id"])
            except Exception:
                raise ValueError("Attached file import is unsupported.") from None
            for reference in file_references:
                _validate_owned_text_file(reference, user_id, row)
        models, rows = [], []
        for item in prepared:
            now = int(time.time())
            chat = item["chat"]
            model = ChatModel(id=str(uuid.uuid4()), user_id=user_id, title=chat.get("title", item.get("title", "New Chat")), chat=chat, created_at=item.get("created_at", now), updated_at=item.get("updated_at", now), share_id=None, archived=False, pinned=item.get("pinned", False), meta={}, folder_id=None)
            models.append(model)
            rows.append(Chat(**model.model_dump()))
        with get_db() as db:
            try:
                db.add_all(rows)
                db.commit()
            except Exception:
                db.rollback()
                raise RuntimeError("Bulk chat import failed.") from None
        return models

    def update_chat_by_id(
        self, id: str, chat: dict, update_timestamp: bool = True
    ) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat_item = db.get(Chat, id)
                chat_item.chat = chat
                chat_item.title = chat["title"] if "title" in chat else "New Chat"
                if update_timestamp:
                    chat_item.updated_at = int(time.time())
                db.commit()
                db.refresh(chat_item)

                return ChatModel.model_validate(chat_item)
        except Exception:
            return None

    def acknowledge_private_saved_chats(self, user_id: str) -> Optional[dict]:
        try:
            with get_db() as db:
                rows = (
                    db.query(
                        Chat.id,
                        Chat.meta,
                        cast(Chat.meta, Text).label("meta_raw"),
                        Chat.updated_at,
                        Chat.share_id,
                        Chat.folder_id,
                    )
                    .filter_by(user_id=user_id)
                    .with_for_update()
                    .all()
                )
                updates = []
                for row in rows:
                    if row.share_id is not None or row.folder_id is not None:
                        return None
                    if type(row.updated_at) is not int or row.updated_at < 0:
                        return None
                    updated_meta = _updated_saved_work_read_meta(
                        row.meta, row.updated_at
                    )
                    if updated_meta is None:
                        return None
                    updates.append((row, updated_meta))

                for row, updated_meta in updates:
                    result = db.execute(
                        update(Chat)
                        .where(
                            Chat.id == row.id,
                            Chat.user_id == user_id,
                            Chat.updated_at == row.updated_at,
                            cast(Chat.meta, Text) == row.meta_raw,
                            Chat.share_id.is_(None),
                            Chat.folder_id.is_(None),
                        )
                        .values(meta=updated_meta)
                    )
                    if result.rowcount != 1:
                        db.rollback()
                        return None
                db.commit()
                return {
                    "updated_count": len(updates),
                    "folder_unread_counts": {},
                }
        except Exception:
            return None

    def update_chat_title_by_id(self, id: str, title: str) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        chat = chat.chat
        chat["title"] = title

        return self.update_chat_by_id(id, chat)

    def update_chat_tags_by_id(
        self, id: str, tags: list[str], user
    ) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        self.delete_all_tags_by_id_and_user_id(id, user.id)

        for tag in chat.meta.get("tags", []):
            if self.count_chats_by_tag_name_and_user_id(tag, user.id) == 0:
                Tags.delete_tag_by_name_and_user_id(tag, user.id)

        for tag_name in tags:
            if tag_name.lower() == "none":
                continue

            self.add_chat_tag_by_id_and_user_id_and_tag_name(id, user.id, tag_name)
        return self.get_chat_by_id(id)

    def get_chat_title_by_id(self, id: str) -> Optional[str]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        return chat.chat.get("title", "New Chat")

    def get_messages_by_chat_id(self, id: str) -> Optional[dict]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        return chat.chat.get("history", {}).get("messages", {}) or {}

    def get_message_by_id_and_message_id(
        self, id: str, message_id: str
    ) -> Optional[dict]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        return chat.chat.get("history", {}).get("messages", {}).get(message_id, {})

    def upsert_message_to_chat_by_id_and_message_id(
        self, id: str, message_id: str, message: dict
    ) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        chat = chat.chat
        history = chat.get("history", {})

        if message_id in history.get("messages", {}):
            history["messages"][message_id] = {
                **history["messages"][message_id],
                **message,
            }
        else:
            history["messages"][message_id] = message

        history["currentId"] = message_id

        chat["history"] = history
        return self.update_chat_by_id(id, chat)

    def add_message_status_to_chat_by_id_and_message_id(
        self, id: str, message_id: str, status: dict
    ) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        chat = chat.chat
        history = chat.get("history", {})

        if message_id in history.get("messages", {}):
            status_history = history["messages"][message_id].get("statusHistory", [])
            status_history.append(status)
            history["messages"][message_id]["statusHistory"] = status_history

        chat["history"] = history
        return self.update_chat_by_id(id, chat)

    def insert_shared_chat_by_chat_id(self, chat_id: str) -> Optional[ChatModel]:
        with get_db() as db:
            # Get the existing chat to share
            chat = db.get(Chat, chat_id)
            # Check if the chat is already shared
            if chat.share_id:
                return self.get_chat_by_id_and_user_id(chat.share_id, "shared")
            # Create a new chat with the same data, but with a new ID
            shared_chat = ChatModel(
                **{
                    "id": str(uuid.uuid4()),
                    "user_id": f"shared-{chat_id}",
                    "title": chat.title,
                    "chat": chat.chat,
                    "created_at": chat.created_at,
                    "updated_at": int(time.time()),
                }
            )
            shared_result = Chat(**shared_chat.model_dump())
            db.add(shared_result)
            db.commit()
            db.refresh(shared_result)

            # Update the original chat with the share_id
            result = (
                db.query(Chat)
                .filter_by(id=chat_id)
                .update({"share_id": shared_chat.id})
            )
            db.commit()
            return shared_chat if (shared_result and result) else None

    def update_shared_chat_by_chat_id(self, chat_id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, chat_id)
                shared_chat = (
                    db.query(Chat).filter_by(user_id=f"shared-{chat_id}").first()
                )

                if shared_chat is None:
                    return self.insert_shared_chat_by_chat_id(chat_id)

                shared_chat.title = chat.title
                shared_chat.chat = chat.chat

                shared_chat.updated_at = int(time.time())
                db.commit()
                db.refresh(shared_chat)

                return ChatModel.model_validate(shared_chat)
        except Exception:
            return None

    def delete_shared_chat_by_chat_id(self, chat_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(user_id=f"shared-{chat_id}").delete()
                db.commit()

                return True
        except Exception:
            return False

    def update_chat_share_id_by_id(
        self, id: str, share_id: Optional[str]
    ) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.share_id = share_id
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def toggle_chat_pinned_by_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.pinned = not chat.pinned
                chat.updated_at = int(time.time())
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def toggle_chat_archive_by_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.archived = not chat.archived
                chat.updated_at = int(time.time())
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def archive_all_chats_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(user_id=user_id).update({"archived": True})
                db.commit()
                return True
        except Exception:
            return False

    def get_archived_chat_list_by_user_id(
        self, user_id: str, skip: int = 0, limit: int = 50
    ) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, archived=True)
                .order_by(Chat.updated_at.desc())
                # .limit(limit).offset(skip)
                .all()
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_list_by_user_id(
        self,
        user_id: str,
        include_archived: bool = False,
        skip: int = 0,
        limit: int = 50,
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter_by(user_id=user_id)
            if not include_archived:
                query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc())

            if skip:
                query = query.offset(skip)
            if limit:
                query = query.limit(limit)

            all_chats = query.all()
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_title_id_list_by_user_id(
        self,
        user_id: str,
        include_archived: bool = False,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[PrivateChatTitleIdResponse]:
        with get_db() as db:
            query = db.query(Chat).filter_by(user_id=user_id).filter_by(folder_id=None)
            query = query.filter(or_(Chat.pinned == False, Chat.pinned == None))

            if not include_archived:
                query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc()).with_entities(
                Chat.id,
                Chat.title,
                Chat.updated_at,
                Chat.created_at,
                Chat.meta,
                Chat.archived,
            )

            if skip:
                query = query.offset(skip)
            if limit:
                query = query.limit(limit)

            all_chats = query.all()

            # result has to be destrctured from sqlalchemy `row` and mapped to a dict since the `ChatModel`is not the returned dataclass.
            return [
                PrivateChatTitleIdResponse.model_validate(
                    {
                        "id": chat[0],
                        "title": chat[1],
                        "updated_at": chat[2],
                        "created_at": chat[3],
                        "last_read_at": _saved_work_last_read(chat[4]),
                        "snippet": None,
                        "active": False,
                        "archived": bool(chat[5]),
                    }
                )
                for chat in all_chats
            ]

    def get_chat_list_by_chat_ids(
        self, chat_ids: list[str], skip: int = 0, limit: int = 50
    ) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter(Chat.id.in_(chat_ids))
                .filter_by(archived=False)
                .order_by(Chat.updated_at.desc())
                .all()
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_by_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def get_chat_by_share_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                # it is possible that the shared link was deleted. hence,
                # we check if the chat is still shared by checking if a chat with the share_id exists
                chat = db.query(Chat).filter_by(share_id=id).first()

                if chat:
                    return self.get_chat_by_id(id)
                else:
                    return None
        except Exception:
            return None

    def get_chat_by_id_and_user_id(self, id: str, user_id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.query(Chat).filter_by(id=id, user_id=user_id).first()
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def get_chats(self, skip: int = 0, limit: int = 50) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                # .limit(limit).offset(skip)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_pinned_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, pinned=True, archived=False)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_archived_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, archived=True)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_user_id_and_search_text(
        self,
        user_id: str,
        search_text: str,
        include_archived: bool = False,
        skip: int = 0,
        limit: int = 60,
    ) -> list[ChatModel]:
        """
        Filters chats based on a search query using Python, allowing pagination using skip and limit.
        """
        search_text = search_text.lower().strip()

        if not search_text:
            return self.get_chat_list_by_user_id(user_id, include_archived, skip, limit)

        search_text_words = search_text.split(" ")

        # search_text might contain 'tag:tag_name' format so we need to extract the tag_name, split the search_text and remove the tags
        tag_ids = [
            word.replace("tag:", "").replace(" ", "_").lower()
            for word in search_text_words
            if word.startswith("tag:")
        ]

        search_text_words = [
            word for word in search_text_words if not word.startswith("tag:")
        ]

        search_text = " ".join(search_text_words)

        with get_db() as db:
            query = db.query(Chat).filter(Chat.user_id == user_id)

            if not include_archived:
                query = query.filter(Chat.archived == False)

            query = query.order_by(Chat.updated_at.desc())

            # Check if the database dialect is either 'sqlite' or 'postgresql'
            dialect_name = db.bind.dialect.name
            if dialect_name == "sqlite":
                # SQLite case: using JSON1 extension for JSON searching
                query = query.filter(
                    (
                        Chat.title.ilike(
                            f"%{search_text}%"
                        )  # Case-insensitive search in title
                        | text(
                            """
                            EXISTS (
                                SELECT 1 
                                FROM json_each(Chat.chat, '$.messages') AS message 
                                WHERE LOWER(message.value->>'content') LIKE '%' || :search_text || '%'
                            )
                            """
                        )
                    ).params(search_text=search_text)
                )

                # Check if there are any tags to filter, it should have all the tags
                if "none" in tag_ids:
                    query = query.filter(
                        text(
                            """
                            NOT EXISTS (
                                SELECT 1
                                FROM json_each(Chat.meta, '$.tags') AS tag
                            )
                            """
                        )
                    )
                elif tag_ids:
                    query = query.filter(
                        and_(
                            *[
                                text(
                                    f"""
                                    EXISTS (
                                        SELECT 1
                                        FROM json_each(Chat.meta, '$.tags') AS tag
                                        WHERE tag.value = :tag_id_{tag_idx}
                                    )
                                    """
                                ).params(**{f"tag_id_{tag_idx}": tag_id})
                                for tag_idx, tag_id in enumerate(tag_ids)
                            ]
                        )
                    )

            elif dialect_name == "postgresql":
                # PostgreSQL relies on proper JSON query for search
                query = query.filter(
                    (
                        Chat.title.ilike(
                            f"%{search_text}%"
                        )  # Case-insensitive search in title
                        | text(
                            """
                            EXISTS (
                                SELECT 1
                                FROM json_array_elements(Chat.chat->'messages') AS message
                                WHERE LOWER(message->>'content') LIKE '%' || :search_text || '%'
                            )
                            """
                        )
                    ).params(search_text=search_text)
                )

                # Check if there are any tags to filter, it should have all the tags
                if "none" in tag_ids:
                    query = query.filter(
                        text(
                            """
                            NOT EXISTS (
                                SELECT 1
                                FROM json_array_elements_text(Chat.meta->'tags') AS tag
                            )
                            """
                        )
                    )
                elif tag_ids:
                    query = query.filter(
                        and_(
                            *[
                                text(
                                    f"""
                                    EXISTS (
                                        SELECT 1
                                        FROM json_array_elements_text(Chat.meta->'tags') AS tag
                                        WHERE tag = :tag_id_{tag_idx}
                                    )
                                    """
                                ).params(**{f"tag_id_{tag_idx}": tag_id})
                                for tag_idx, tag_id in enumerate(tag_ids)
                            ]
                        )
                    )
            else:
                raise NotImplementedError(
                    f"Unsupported dialect: {db.bind.dialect.name}"
                )

            # Perform pagination at the SQL level
            all_chats = query.offset(skip).limit(limit).all()

            log.info(f"The number of chats: {len(all_chats)}")

            # Validate and return chats
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_folder_id_and_user_id(
        self, folder_id: str, user_id: str
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter_by(folder_id=folder_id, user_id=user_id)
            query = query.filter(or_(Chat.pinned == False, Chat.pinned == None))
            query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc())

            all_chats = query.all()
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_folder_ids_and_user_id(
        self, folder_ids: list[str], user_id: str
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter(
                Chat.folder_id.in_(folder_ids), Chat.user_id == user_id
            )
            query = query.filter(or_(Chat.pinned == False, Chat.pinned == None))
            query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc())

            all_chats = query.all()
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def update_chat_folder_id_by_id_and_user_id(
        self, id: str, user_id: str, folder_id: str
    ) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.folder_id = folder_id
                chat.updated_at = int(time.time())
                chat.pinned = False
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def get_chat_tags_by_id_and_user_id(self, id: str, user_id: str) -> list[TagModel]:
        with get_db() as db:
            chat = db.get(Chat, id)
            tags = chat.meta.get("tags", [])
            return [Tags.get_tag_by_name_and_user_id(tag, user_id) for tag in tags]

    def get_chat_list_by_user_id_and_tag_name(
        self, user_id: str, tag_name: str, skip: int = 0, limit: int = 50
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter_by(user_id=user_id)
            tag_id = tag_name.replace(" ", "_").lower()

            log.info(f"DB dialect name: {db.bind.dialect.name}")
            if db.bind.dialect.name == "sqlite":
                # SQLite JSON1 querying for tags within the meta JSON field
                query = query.filter(
                    text(
                        f"EXISTS (SELECT 1 FROM json_each(Chat.meta, '$.tags') WHERE json_each.value = :tag_id)"
                    )
                ).params(tag_id=tag_id)
            elif db.bind.dialect.name == "postgresql":
                # PostgreSQL JSON query for tags within the meta JSON field (for `json` type)
                query = query.filter(
                    text(
                        "EXISTS (SELECT 1 FROM json_array_elements_text(Chat.meta->'tags') elem WHERE elem = :tag_id)"
                    )
                ).params(tag_id=tag_id)
            else:
                raise NotImplementedError(
                    f"Unsupported dialect: {db.bind.dialect.name}"
                )

            all_chats = query.all()
            log.debug(f"all_chats: {all_chats}")
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def add_chat_tag_by_id_and_user_id_and_tag_name(
        self, id: str, user_id: str, tag_name: str
    ) -> Optional[ChatModel]:
        tag = Tags.get_tag_by_name_and_user_id(tag_name, user_id)
        if tag is None:
            tag = Tags.insert_new_tag(tag_name, user_id)
        try:
            with get_db() as db:
                chat = db.get(Chat, id)

                tag_id = tag.id
                if tag_id not in chat.meta.get("tags", []):
                    chat.meta = {
                        **chat.meta,
                        "tags": list(set(chat.meta.get("tags", []) + [tag_id])),
                    }

                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def count_chats_by_tag_name_and_user_id(self, tag_name: str, user_id: str) -> int:
        with get_db() as db:  # Assuming `get_db()` returns a session object
            query = db.query(Chat).filter_by(user_id=user_id, archived=False)

            # Normalize the tag_name for consistency
            tag_id = tag_name.replace(" ", "_").lower()

            if db.bind.dialect.name == "sqlite":
                # SQLite JSON1 support for querying the tags inside the `meta` JSON field
                query = query.filter(
                    text(
                        f"EXISTS (SELECT 1 FROM json_each(Chat.meta, '$.tags') WHERE json_each.value = :tag_id)"
                    )
                ).params(tag_id=tag_id)

            elif db.bind.dialect.name == "postgresql":
                # PostgreSQL JSONB support for querying the tags inside the `meta` JSON field
                query = query.filter(
                    text(
                        "EXISTS (SELECT 1 FROM json_array_elements_text(Chat.meta->'tags') elem WHERE elem = :tag_id)"
                    )
                ).params(tag_id=tag_id)

            else:
                raise NotImplementedError(
                    f"Unsupported dialect: {db.bind.dialect.name}"
                )

            # Get the count of matching records
            count = query.count()

            # Debugging output for inspection
            log.info(f"Count of chats for tag '{tag_name}': {count}")

            return count

    def delete_tag_by_id_and_user_id_and_tag_name(
        self, id: str, user_id: str, tag_name: str
    ) -> bool:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                tags = chat.meta.get("tags", [])
                tag_id = tag_name.replace(" ", "_").lower()

                tags = [tag for tag in tags if tag != tag_id]
                chat.meta = {
                    **chat.meta,
                    "tags": list(set(tags)),
                }
                db.commit()
                return True
        except Exception:
            return False

    def delete_all_tags_by_id_and_user_id(self, id: str, user_id: str) -> bool:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.meta = {
                    **chat.meta,
                    "tags": [],
                }
                db.commit()

                return True
        except Exception:
            return False

    def delete_chat_by_id(self, id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(id=id).delete()
                db.commit()

                return True and self.delete_shared_chat_by_chat_id(id)
        except Exception:
            return False

    def delete_chat_by_id_and_user_id(self, id: str, user_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(id=id, user_id=user_id).delete()
                db.commit()

                return True and self.delete_shared_chat_by_chat_id(id)
        except Exception:
            return False

    def delete_chats_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                self.delete_shared_chats_by_user_id(user_id)

                db.query(Chat).filter_by(user_id=user_id).delete()
                db.commit()

                return True
        except Exception:
            return False

    def delete_chats_by_user_id_and_folder_id(
        self, user_id: str, folder_id: str
    ) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(user_id=user_id, folder_id=folder_id).delete()
                db.commit()

                return True
        except Exception:
            return False

    def delete_shared_chats_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                chats_by_user = db.query(Chat).filter_by(user_id=user_id).all()
                shared_chat_ids = [f"shared-{chat.id}" for chat in chats_by_user]

                db.query(Chat).filter(Chat.user_id.in_(shared_chat_ids)).delete()
                db.commit()

                return True
        except Exception:
            return False


Chats = ChatTable()
