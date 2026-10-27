# tasks.py
import asyncio
import logging
from typing import Dict
from uuid import uuid4

# A dictionary to keep track of active tasks
tasks: Dict[str, asyncio.Task] = {}
chat_tasks = {}
task_owners: Dict[str, str] = {}


class TaskNotFoundError(ValueError):
    """The requested task is no longer present in the active registry."""


def cleanup_task(task_id: str, id=None):
    """
    Remove a completed or canceled task from the global `tasks` dictionary.
    """
    tasks.pop(task_id, None)  # Remove the task if it exists
    task_owners.pop(task_id, None)
    if not task_owners:
        task_owners.clear()

    # If an ID is provided, remove the task from the chat_tasks dictionary
    if id and task_id in chat_tasks.get(id, []):
        chat_tasks[id].remove(task_id)
        if not chat_tasks[id]:  # If no tasks left for this ID, remove the entry
            chat_tasks.pop(id, None)


def _task_done(task, task_id: str, id=None):
    try:
        if not task.cancelled() and task.exception() is not None:
            logging.getLogger(__name__).warning(
                "A background task failed."
            )
    except asyncio.CancelledError:
        pass
    finally:
        cleanup_task(task_id, id)


def create_task(coroutine, id=None, *, owner_user_id=None):
    """
    Create a new asyncio task and add it to the global task dictionary.
    """
    task_id = str(uuid4())  # Generate a unique ID for the task
    task = asyncio.create_task(coroutine)  # Create the task

    # Retrieve unexpected task failures and remove completed task references.
    task.add_done_callback(lambda t: _task_done(t, task_id, id))
    tasks[task_id] = task

    # If an ID is provided, associate the task with that ID
    if chat_tasks.get(id):
        chat_tasks[id].append(task_id)
    else:
        chat_tasks[id] = [task_id]

    if (
        isinstance(id, str)
        and id.startswith("local:")
        and isinstance(owner_user_id, str)
        and owner_user_id.strip()
    ):
        task_owners[task_id] = owner_user_id

    return task_id, task


def get_task(task_id: str):
    """
    Retrieve a task by its task ID.
    """
    return tasks.get(task_id)


def list_tasks():
    """
    List all currently active task IDs.
    """
    return list(tasks.keys())


def list_task_ids_by_chat_id(id):
    """
    List all tasks associated with a specific ID.
    """
    return chat_tasks.get(id, [])


def list_task_ids_by_chat_id_for_user(id, user_id):
    if (
        not isinstance(id, str)
        or not id.startswith("local:")
        or not isinstance(user_id, str)
        or not user_id.strip()
    ):
        return []
    return [
        task_id
        for task_id in chat_tasks.get(id, [])
        if task_owners.get(task_id) == user_id
    ]


async def stop_task(task_id: str):
    """
    Cancel a running task and remove it from the global task list.
    """
    task = tasks.get(task_id)
    if not task:
        raise TaskNotFoundError(f"Task with ID {task_id} not found.")

    cancellation_requested = task.cancel()
    try:
        await task  # Wait for the task to handle the cancellation
    except asyncio.CancelledError:
        tasks.pop(task_id, None)
        if cancellation_requested:
            return {"status": True, "message": f"Task {task_id} successfully stopped."}
        return {"status": False, "message": f"Task {task_id} was already stopping."}

    if cancellation_requested:
        tasks.pop(task_id, None)
        return {"status": True, "message": f"Task {task_id} successfully stopped."}

    return {"status": False, "message": f"Failed to stop task {task_id}."}
