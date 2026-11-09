from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import logging
from typing import Literal, Optional

from open_webui.models.memories import Memories, MemoryModel, OpenMapping
from open_webui.models.users import Users
from open_webui.retrieval.vector.connector import VECTOR_DB_CLIENT
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.env import SRC_LOG_LEVELS


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()

MemoryType = Literal["user", "context"]
MemoryTypeFilter = Literal["user", "context", "all"]
MAX_MEMORY_RESULTS = 1000
MAX_MEMORY_OPERATIONS = 100


def _bounded_limit(value: int) -> int:
    return max(0, min(value, MAX_MEMORY_RESULTS))


def _memory_metadata(memory: MemoryModel) -> dict:
    metadata = {
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "type": memory.type,
    }
    if memory.path is not None:
        metadata["path"] = memory.path
    return metadata


def _upsert_memory_vector(request: Request, memory: MemoryModel, user) -> None:
    VECTOR_DB_CLIENT.upsert(
        collection_name=f"user-memory-{memory.user_id}",
        items=[
            {
                "id": memory.id,
                "text": memory.content,
                "vector": request.app.state.EMBEDDING_FUNCTION(
                    memory.content, user=user
                ),
                "metadata": _memory_metadata(memory),
            }
        ],
    )


def _memory_matches(
    memory: MemoryModel,
    *,
    memory_type: MemoryTypeFilter,
    path: Optional[str] = None,
    include_children: bool = True,
) -> bool:
    if memory_type != "all" and memory.type != memory_type:
        return False
    if path is None:
        return True
    if memory.path is None:
        return False
    if memory.path == path:
        return True
    return include_children and memory.path.startswith(path.rstrip("/") + "/")


@router.get("/ef")
async def get_embeddings(request: Request):
    return {"result": request.app.state.EMBEDDING_FUNCTION("hello world")}


############################
# GetMemories
############################


@router.get("/", response_model=list[MemoryModel])
async def get_memories(user=Depends(get_verified_user)):
    return Memories.get_memories_by_user_id(user.id)


############################
# AddMemory
############################


class AddMemoryForm(BaseModel):
    content: str


class MemoryUpdateModel(BaseModel):
    content: Optional[str] = None


@router.post("/add", response_model=Optional[MemoryModel])
async def add_memory(
    request: Request,
    form_data: AddMemoryForm,
    user=Depends(get_verified_user),
):
    memory = Memories.insert_new_memory(user.id, form_data.content)

    VECTOR_DB_CLIENT.upsert(
        collection_name=f"user-memory-{user.id}",
        items=[
            {
                "id": memory.id,
                "text": memory.content,
                "vector": request.app.state.EMBEDDING_FUNCTION(
                    memory.content, user=user
                ),
                "metadata": {"created_at": memory.created_at},
            }
        ],
    )

    return memory


############################
# Current memory compatibility operations
############################


class MemoryOperationModel(BaseModel):
    action: Literal["add", "replace", "remove", "move"]
    content: Optional[str] = None
    id: Optional[str] = None
    path: Optional[str] = None
    type: Optional[MemoryType] = None


class UpdateMemoriesForm(BaseModel):
    operations: list[MemoryOperationModel]
    source: Optional[Literal["tool", "background_review"]] = None


class SearchMemoriesForm(BaseModel):
    memory_id: Optional[str] = None
    query: Optional[str] = None
    path: Optional[str] = None
    type: MemoryTypeFilter = "all"
    limit: int = 20


class ReadMemoryPathForm(BaseModel):
    path: str
    type: MemoryTypeFilter = "all"
    include_children: bool = True
    limit: int = 50


class ListMemoryPathsForm(BaseModel):
    query: Optional[str] = None
    type: MemoryTypeFilter = "all"
    limit: int = 100


@router.post("/search", response_model=list[MemoryModel])
async def search_memories(
    request: Request,
    form_data: SearchMemoriesForm,
    user=Depends(get_verified_user),
):
    memories = Memories.get_memories_by_user_id(user.id) or []
    if form_data.memory_id is not None:
        memories = [memory for memory in memories if memory.id == form_data.memory_id]

    memories = [
        memory
        for memory in memories
        if _memory_matches(
            memory,
            memory_type=form_data.type,
            path=form_data.path,
            include_children=True,
        )
    ]

    if form_data.query:
        results = VECTOR_DB_CLIENT.search(
            collection_name=f"user-memory-{user.id}",
            vectors=[request.app.state.EMBEDDING_FUNCTION(form_data.query, user=user)],
            limit=_bounded_limit(form_data.limit),
        )
        ids = results.ids[0] if results and results.ids else []
        positions = {memory_id: index for index, memory_id in enumerate(ids)}
        vector_matches = [memory for memory in memories if memory.id in positions]
        if vector_matches:
            memories = sorted(vector_matches, key=lambda memory: positions[memory.id])
        else:
            query = form_data.query.casefold()
            memories = [memory for memory in memories if query in memory.content.casefold()]

    return memories[: _bounded_limit(form_data.limit)]


@router.post("/paths")
async def list_memory_paths(
    form_data: ListMemoryPathsForm,
    user=Depends(get_verified_user),
):
    memories = [
        memory
        for memory in (Memories.get_memories_by_user_id(user.id) or [])
        if memory.path is not None
        and _memory_matches(memory, memory_type=form_data.type)
    ]
    grouped = {}
    query = form_data.query.casefold() if form_data.query else None
    for memory in memories:
        if query and query not in memory.path.casefold():
            continue
        key = (memory.path, memory.type)
        row = grouped.setdefault(
            key,
            {
                "path": memory.path,
                "type": memory.type,
                "count": 0,
                "updated_at": memory.updated_at,
                "children": [],
            },
        )
        row["count"] += 1
        row["updated_at"] = max(row["updated_at"], memory.updated_at)

    rows = list(grouped.values())
    paths = {row["path"] for row in rows}
    for row in rows:
        prefix = row["path"].rstrip("/") + "/"
        row["children"] = sorted(path for path in paths if path.startswith(prefix))
    rows.sort(key=lambda row: (row["path"], row["type"]))
    rows = rows[: _bounded_limit(form_data.limit)]
    return {"count": len(rows), "paths": rows}


@router.post("/path")
async def read_memory_path(
    form_data: ReadMemoryPathForm,
    user=Depends(get_verified_user),
):
    all_memories = [
        memory
        for memory in (Memories.get_memories_by_user_id(user.id) or [])
        if memory.path is not None
        and _memory_matches(memory, memory_type=form_data.type)
    ]
    memories = [
        memory
        for memory in all_memories
        if _memory_matches(
            memory,
            memory_type=form_data.type,
            path=form_data.path,
            include_children=form_data.include_children,
        )
    ][:_bounded_limit(form_data.limit)]
    paths = {memory.path for memory in all_memories}
    prefix = form_data.path.rstrip("/") + "/"
    return {
        "path": form_data.path,
        "parents": sorted(
            path for path in paths if form_data.path.startswith(path.rstrip("/") + "/")
        ),
        "children": sorted(path for path in paths if path.startswith(prefix)),
        "memories": memories,
    }


@router.post("/update", response_model=list[OpenMapping])
async def update_memories(
    request: Request,
    form_data: UpdateMemoriesForm,
    user=Depends(get_verified_user),
):
    if len(form_data.operations) > MAX_MEMORY_OPERATIONS:
        raise HTTPException(status_code=400, detail="Too many memory operations")

    results = []
    for operation in form_data.operations:
        if operation.action == "add":
            if operation.content is None:
                raise HTTPException(status_code=400, detail="Memory content is required")
            memory = Memories.insert_new_memory(
                user.id,
                operation.content,
                path=operation.path,
                type=operation.type or "context",
                meta={
                    "chat_id": None,
                    "message_id": None,
                    "model": None,
                    "created_by": form_data.source,
                },
            )
            _upsert_memory_vector(request, memory, user)
            results.append({"action": "add", "status": "created", "memory": memory})
            continue

        if operation.id is None:
            raise HTTPException(status_code=400, detail="Memory id is required")
        current = Memories.get_memory_by_id_and_user_id(operation.id, user.id)
        if current is None:
            raise HTTPException(status_code=404, detail="Memory not found")

        if operation.action == "remove":
            Memories.delete_memory_by_id_and_user_id(operation.id, user.id)
            VECTOR_DB_CLIENT.delete(
                collection_name=f"user-memory-{user.id}", ids=[operation.id]
            )
            results.append({"action": "remove", "status": "deleted", "id": operation.id})
            continue

        if operation.action == "replace" and operation.content is None:
            raise HTTPException(status_code=400, detail="Memory content is required")

        memory = Memories.update_memory_by_id_and_user_id(
            operation.id,
            user.id,
            operation.content if operation.action == "replace" else None,
            path=operation.path,
            update_path=True,
            type=operation.type,
        )
        if memory is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        _upsert_memory_vector(request, memory, user)
        results.append({"action": operation.action, "status": "updated", "memory": memory})

    return results


@router.post("/reindex")
async def reindex_memories_from_vector_db(
    request: Request,
    user=Depends(get_admin_user),
):
    memories = Memories.get_memories() or []
    user_ids = {memory.user_id for memory in memories}
    for user_id in user_ids:
        try:
            VECTOR_DB_CLIENT.delete_collection(f"user-memory-{user_id}")
        except Exception:
            # A copied database can legitimately have no vector collection yet.
            pass
    for memory in memories:
        owner = Users.get_user_by_id(memory.user_id) or user
        _upsert_memory_vector(request, memory, owner)
    return {
        "status": True,
        "total_memories": len(memories),
        "total_users": Users.get_num_users() or 0,
    }


############################
# QueryMemory
############################


class QueryMemoryForm(BaseModel):
    content: str
    k: Optional[int] = 1


@router.post("/query")
async def query_memory(
    request: Request, form_data: QueryMemoryForm, user=Depends(get_verified_user)
):
    results = VECTOR_DB_CLIENT.search(
        collection_name=f"user-memory-{user.id}",
        vectors=[request.app.state.EMBEDDING_FUNCTION(form_data.content, user=user)],
        limit=form_data.k,
    )

    return results


############################
# ResetMemoryFromVectorDB
############################
@router.post("/reset", response_model=bool)
async def reset_memory_from_vector_db(
    request: Request, user=Depends(get_verified_user)
):
    VECTOR_DB_CLIENT.delete_collection(f"user-memory-{user.id}")

    memories = Memories.get_memories_by_user_id(user.id)
    VECTOR_DB_CLIENT.upsert(
        collection_name=f"user-memory-{user.id}",
        items=[
            {
                "id": memory.id,
                "text": memory.content,
                "vector": request.app.state.EMBEDDING_FUNCTION(
                    memory.content, user=user
                ),
                "metadata": {
                    "created_at": memory.created_at,
                    "updated_at": memory.updated_at,
                },
            }
            for memory in memories
        ],
    )

    return True


############################
# DeleteMemoriesByUserId
############################


@router.delete("/delete/user", response_model=bool)
async def delete_memory_by_user_id(user=Depends(get_verified_user)):
    result = Memories.delete_memories_by_user_id(user.id)

    if result:
        try:
            VECTOR_DB_CLIENT.delete_collection(f"user-memory-{user.id}")
        except Exception as e:
            log.error(e)
        return True

    return False


############################
# UpdateMemoryById
############################


@router.post("/{memory_id}/update", response_model=Optional[MemoryModel])
async def update_memory_by_id(
    memory_id: str,
    request: Request,
    form_data: MemoryUpdateModel,
    user=Depends(get_verified_user),
):
    memory = Memories.update_memory_by_id_and_user_id(
        memory_id, user.id, form_data.content
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    if form_data.content is not None:
        VECTOR_DB_CLIENT.upsert(
            collection_name=f"user-memory-{user.id}",
            items=[
                {
                    "id": memory.id,
                    "text": memory.content,
                    "vector": request.app.state.EMBEDDING_FUNCTION(
                        memory.content, user=user
                    ),
                    "metadata": {
                        "created_at": memory.created_at,
                        "updated_at": memory.updated_at,
                    },
                }
            ],
        )

    return memory


############################
# DeleteMemoryById
############################


@router.delete("/{memory_id}", response_model=bool)
async def delete_memory_by_id(memory_id: str, user=Depends(get_verified_user)):
    result = Memories.delete_memory_by_id_and_user_id(memory_id, user.id)

    if result:
        VECTOR_DB_CLIENT.delete(
            collection_name=f"user-memory-{user.id}", ids=[memory_id]
        )
        return True

    return False
