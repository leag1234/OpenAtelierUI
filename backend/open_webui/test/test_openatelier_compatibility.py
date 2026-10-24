# SPDX-License-Identifier: BSD-3-Clause
import asyncio

from open_webui.models.users import UserModel


def synthetic_user():
    return UserModel(
        id="synthetic-user",
        name="Synthetic User",
        email="user@synthetic.invalid",
        role="user",
        profile_image_url="/user.png",
        last_active_at=1,
        updated_at=1,
        created_at=1,
    )


def test_user_model_keeps_native_sync_access_and_is_awaitable():
    user = synthetic_user()

    # Existing synchronous callers still receive the concrete model and fields.
    assert user.email == "user@synthetic.invalid"
    assert isinstance(user, UserModel)

    # The unchanged async fixture can await the result from the same accessor.
    resolved = asyncio.run(resolve(user))
    assert resolved is user
    assert resolved.email == "user@synthetic.invalid"


async def resolve(user):
    return await user
