# SPDX-License-Identifier: BSD-3-Clause
import asyncio
import importlib.util
import pathlib
import sys
import time
import types


OPEN_WEBUI = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "openatelier_plugin_user_compat",
    OPEN_WEBUI / "utils" / "plugin_user_compat.py",
)
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)


class SyntheticUser:
    email = "user@synthetic.invalid"


class SyntheticUsers:
    def __init__(self, value):
        self.value = value

    def get_user_by_id(self, user_id):
        time.sleep(0.06)
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


async def assert_heartbeat_during(awaitable):
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    task = asyncio.create_task(heartbeat())
    try:
        result = await awaitable
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert ticks >= 3
    return result


def test_unmodified_sync_pipe_lookup_runs_off_loop_and_reads_email():
    users = SyntheticUsers(SyntheticUser())
    module = types.ModuleType("synthetic_sync_plugin")
    module.Users = users
    module.Pipe = type("Pipe", (), {})
    assert compat.adapt_pipe_module(module, users)
    pipe_type = type("Pipe", (), {"__module__": module.__name__})
    pipe = pipe_type()

    def unchanged_pipe():
        return module.Users.get_user_by_id("synthetic-user").email

    async def execute():
        return await assert_heartbeat_during(
            compat.execute_pipe(unchanged_pipe, {}, lambda _: module)
        )

    assert asyncio.run(execute()) == "user@synthetic.invalid"


def test_sync_accessor_preserves_native_model_identity_and_none():
    user = SyntheticUser()
    users = SyntheticUsers(user)
    module = types.ModuleType("synthetic_sync_plugin")
    module.Users = users
    module.Pipe = type("Pipe", (), {})
    assert compat.adapt_pipe_module(module, users)
    assert module.Users.get_user_by_id("synthetic-user") is user

    users.value = None
    assert module.Users.get_user_by_id("missing") is None


def test_import_alias_is_adapted_by_singleton_identity():
    users = SyntheticUsers(SyntheticUser())
    module = types.ModuleType("synthetic_alias_plugin")
    module.LocalUsers = users
    module.Pipe = type("Pipe", (), {})

    assert compat.adapt_pipe_module(module, users)
    assert (
        module.LocalUsers.get_user_by_id("synthetic-user").email
        == "user@synthetic.invalid"
    )


def test_unmodified_async_pipe_awaits_lookup_off_loop_and_reads_email():
    users = SyntheticUsers(SyntheticUser())
    module = types.ModuleType("synthetic_async_plugin")
    module.Users = users
    module.Pipe = type("Pipe", (), {})
    assert compat.adapt_pipe_module(module, users)

    async def unchanged_pipe():
        user = await module.Users.get_user_by_id("synthetic-user")
        return user.email

    async def execute():
        return await assert_heartbeat_during(
            compat.execute_pipe(unchanged_pipe, {}, lambda _: module)
        )

    assert asyncio.run(execute()) == "user@synthetic.invalid"


def test_async_not_found_and_lookup_errors_keep_native_outcomes():
    async def resolve(value):
        users = SyntheticUsers(value)
        module = types.ModuleType("synthetic_plugin")
        module.Users = users
        module.Pipe = type("Pipe", (), {})
        compat.adapt_pipe_module(module, users)
        return await module.Users.get_user_by_id("missing")

    assert asyncio.run(resolve(None)) is None
    failure = LookupError("synthetic lookup failure")
    try:
        asyncio.run(resolve(failure))
    except LookupError as error:
        assert error is failure
    else:
        raise AssertionError("lookup exception was swallowed by the adapter")
