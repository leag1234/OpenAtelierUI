# User API key response redaction

The source53 frozen observer found that the ordinary administrator user list
serialized a member's opaque API key. This patch changes response filtering on
only three existing `routers/users.py` routes: `GET /`, `POST /update/role`,
and `POST /{user_id}/update`. It does not change their handlers, their
`UserModel`/ORM definitions, storage, authentication, permissions, key
management endpoints, or key rotation/deletion behavior.

The optional single-user responses exclude the top-level `api_key` field. The
list route uses Pydantic's `{"__all__": {"api_key"}}` exclusion tree so the
field is removed from every list item. It retains the other response-model
fields, including `settings`, `info`, timestamps, and `oauth_sub`. Exclusion is
applied during FastAPI response serialization; the internal model continues to
hold its unchanged key.

`python3 validation/test_api_key_response_redaction.py` runs four source-isolated
tests through installed FastAPI 0.115.6 and Pydantic 2.10.5. It extracts the
actual `UserModel`, form classes, and three route function definitions from the
old source, registers those route definitions on a real FastAPI app, and
exercises HTTP serialization with synthetic models. It verifies list and both
optional single-model response shapes, exact retention of all other model
fields, unchanged internal synthetic key, and response-model exclusion limited
to the three admitted routes.

The first HTTP test showed that a flat `{"api_key"}` exclusion did not redact
items inside a list, even though it worked for both single-model routes. The
list exclusion was corrected to the nested `__all__` form and all four tests
then passed. Full application imports are unavailable in this host test
environment because the existing user-model module import chain requires an
uninstalled `typer` package; no package was installed. Therefore this is a
real FastAPI/Pydantic serialization check over extracted old-source routes,
not full app startup or the primary-owned Hardy observer. Key authentication,
explicit owner key endpoints, rotation/deletion, and unrelated API behavior
were not changed or requalified here.
