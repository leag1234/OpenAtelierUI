# Bounded session-purpose and read-continuity provenance

This source slice implements `TASK-OPENATELIER-SESSION-PURPOSE-001` inside the existing opt-in `OPENATELIER_SESSION_EPOCHS` profile. It preserves the original JWT and expiry on `/api/v1/auths/` only when the same request's JWT dependency has verified the signature, expiry, supported purpose, current session stamp, and account. The request-local state uses a module-owned marker and includes the exact selected token; a marker tied to another account or malformed token/expiry context is refused.

When the profile is enabled, a JWT may omit `typ` for compatibility or provide exactly `typ: "session"`. Any other present value is refused with `401 Invalid token` after normal JWT signature/expiry decoding and before session-store or user lookup. The opt-in dependency captures only integer `exp` or its absence after validation; a present expiry must fit UTC timestamp conversion through year 9999. The session read returns the original token and that original expiry, sets the same token in its cookie, and makes no new token issuance or epoch write. A request admitted before rotation can therefore return only its already-validated token, which a later request will reject after rotation.

Default-off behavior, API-key authentication, and unmarked custom-dependency session reads remain on their previous path. This slice does not claim current-version compatibility for those unmarked paths. It adds no issuer claims, authentication-method labels, MFA assertion, cookie policy, key, algorithm, schema, cache, or default. Signature/expiry checking remains in the existing JWT decoder before the new purpose and state checks.

The primary's safe black-box oracle observed same-token/same-expiry session reads, absent-purpose acceptance, explicit refresh-purpose rejection, and expired-token rejection. Its first actual source36 PyJWT/SQLite control reproduced the candidate's prior wrong-purpose acceptance and renewed-token behavior; that receipt is retained by the primary. The primary owns the corrected actual-library and mounted-runtime replay. This helper's tests are source-extracted with controlled JWT and SQLite fixtures, not full application or FastAPI scheduling tests.

Run the named auth tests with:

```sh
PYTHONPYCACHEPREFIX=/tmp/bsd-epochs-pycache python3 backend/open_webui/test/test_session_epoch_auth.py
```

They cover explicit wrong-purpose values before any store/user access, malformed expiry refusal, exact original token/expiry/cookie return without issuance, stale admitted requests returning no later-epoch credential, cross-account/malformed marked state refusal, and the existing default-off/API-key/session-epoch controls. Primary owns the actual PyJWT, SQLite and mounted application verification; this source test suite is not a current-version certification or a full session security review.
