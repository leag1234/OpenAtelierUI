# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
import asyncio
import importlib.util
import pathlib
import time
import types
import unittest


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

    def get_user_by_id(self, id):
        time.sleep(0.06)
        if isinstance(self.value, BaseException):
            raise self.value
        if isinstance(self.value, dict):
            return next(iter(self.value.values()), None)
        return self.value

    def get_user_by_email(self, email):
        time.sleep(0.06)
        if isinstance(self.value, BaseException):
            raise self.value
        if isinstance(self.value, dict):
            return self.value.get(email)
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
    assert module.Users.get_user_by_id(id="synthetic-user") is user

    users.value = None
    assert module.Users.get_user_by_id(id="missing") is None

    for accessor in (users, module.Users):
        try:
            accessor.get_user_by_id(user_id="invalid-keyword")
        except TypeError as error:
            assert "id" in str(error)
        else:
            raise AssertionError("unexpected keyword was accepted")


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


def test_non_pipe_and_unbound_modules_keep_original_binding():
    users = SyntheticUsers(SyntheticUser())
    non_pipe = types.ModuleType("synthetic_non_pipe")
    non_pipe.Users = users
    assert not compat.adapt_pipe_module(non_pipe, users)
    assert non_pipe.Users is users

    unbound = types.ModuleType("synthetic_unbound_pipe")
    unbound.Pipe = type("Pipe", (), {})
    unbound.Users = SyntheticUsers(SyntheticUser())
    original = unbound.Users
    assert not compat.adapt_pipe_module(unbound, users)
    assert unbound.Users is original


def test_unmodified_async_pipe_awaits_lookup_off_loop_and_reads_email():
    users = SyntheticUsers(SyntheticUser())
    module = types.ModuleType("synthetic_async_plugin")
    module.Users = users
    module.Pipe = type("Pipe", (), {})
    assert compat.adapt_pipe_module(module, users)

    async def unchanged_pipe():
        user = await module.Users.get_user_by_id(id="synthetic-user")
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
        return await module.Users.get_user_by_id(id="missing")

    assert asyncio.run(resolve(None)) is None
    failure = LookupError("synthetic lookup failure")
    try:
        asyncio.run(resolve(failure))
    except LookupError as error:
        assert error is failure
    else:
        raise AssertionError("lookup exception was swallowed by the adapter")


def test_sync_email_accessor_preserves_native_model_none_and_keyword_binding():
    user = SyntheticUser()
    users = SyntheticUsers(user)
    module = types.ModuleType("synthetic_email_sync_plugin")
    module.Users = users
    module.Pipe = type("Pipe", (), {})
    assert compat.adapt_pipe_module(module, users)

    assert module.Users.get_user_by_email("user@synthetic.invalid") is user
    assert module.Users.get_user_by_email(email="user@synthetic.invalid") is user

    def unchanged_pipe():
        return module.Users.get_user_by_email(email="user@synthetic.invalid")

    async def execute():
        return await assert_heartbeat_during(
            compat.execute_pipe(unchanged_pipe, {}, lambda _: module)
        )

    assert asyncio.run(execute()) is user
    users.value = None
    assert module.Users.get_user_by_email(email="missing@synthetic.invalid") is None
    for accessor in (users, module.Users):
        try:
            accessor.get_user_by_email(unknown="invalid-keyword")
        except TypeError as error:
            assert "email" in str(error)
        else:
            raise AssertionError("unexpected keyword was accepted")


def test_async_email_accessor_is_awaitable_off_loop_and_propagates_none_and_error():
    user = SyntheticUser()
    users = SyntheticUsers({"user@synthetic.invalid": user})
    module = types.ModuleType("synthetic_email_async_plugin")
    module.Users = users
    module.Pipe = type("Pipe", (), {})
    assert compat.adapt_pipe_module(module, users)

    async def pipe():
        first = await module.Users.get_user_by_id(id="synthetic-user")
        found = await module.Users.get_user_by_email(first.email)
        missing = await module.Users.get_user_by_email("missing@synthetic.invalid")
        return found, missing

    async def execute():
        return await assert_heartbeat_during(compat.execute_pipe(pipe, {}, lambda _: module))

    found, missing = asyncio.run(execute())
    assert found is user
    assert missing is None

    failure = LookupError("synthetic email lookup failure")
    users.value = failure

    async def failing_pipe():
        return await module.Users.get_user_by_email("user@synthetic.invalid")

    try:
        asyncio.run(failing_pipe())
    except LookupError as error:
        assert error is failure
    else:
        raise AssertionError("lookup exception was swallowed by the adapter")


TESTS = (
    test_unmodified_sync_pipe_lookup_runs_off_loop_and_reads_email,
    test_sync_accessor_preserves_native_model_identity_and_none,
    test_import_alias_is_adapted_by_singleton_identity,
    test_non_pipe_and_unbound_modules_keep_original_binding,
    test_unmodified_async_pipe_awaits_lookup_off_loop_and_reads_email,
    test_async_not_found_and_lookup_errors_keep_native_outcomes,
    test_sync_email_accessor_preserves_native_model_none_and_keyword_binding,
    test_async_email_accessor_is_awaitable_off_loop_and_propagates_none_and_error,
)


if __name__ == "__main__":
    suite = unittest.TestSuite(unittest.FunctionTestCase(test) for test in TESTS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
