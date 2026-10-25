# Bounded auth-settings manifest and admin DTO provenance

This is the candidate implementation slice for `TASK-OPENATELIER-AUTH-SETTINGS-001`. It adds one stateless validator at `validation/auth_settings.py` and adds the existing `ENABLE_LOGIN_FORM` value to the existing admin-only GET config response in `backend/open_webui/routers/auths.py`. It does not write environment variables, config database rows, Redis, or files; change issuer/validator claims, schema, runtime defaults, or persistent-config policy; or perform cutover.

The validator accepts only the exact `openatelier-auth-settings/v1` object with exactly three settings: `JWT_EXPIRES_IN`, `ENABLE_SIGNUP`, and `ENABLE_LOGIN_FORM`. Duration validation uses a full match for one positive integer and one `s`, `m`, `h`, `d`, or `w` unit, with a maximum of 366 days and 32 characters. Booleans must be JSON booleans. Duplicate keys are rejected at every object level; invalid JSON/UTF-8/constants, missing/extra fields, invalid types, nonpositive/unsupported/oversized durations, and expiries above the cap return named refusals without echoing arbitrary input.

File input must be a regular non-symlink file no larger than 16 KiB. The reader checks metadata before opening, uses `O_NOFOLLOW` when available, verifies the opened descriptor, and reads at most the size limit plus one byte. Deeply nested JSON that exceeds the parser's recursion capacity and invalid path values such as embedded NUL are converted to named JSON-only refusals. The CLI is invoked as:

```sh
python3 validation/auth_settings.py --manifest PATH
```

Success emits one JSON object with only the fixed `environment` mapping, derived `ttl_seconds`, `manifest_sha256`, `partial: true`, and `cutover: false`. The three environment values are strings; booleans are serialized as lowercase `true` or `false`. Refusal emits JSON with a stable code, `partial: true`, and `cutover: false`, and exits nonzero. The validated map is output data only; the CLI never executes it or mutates the calling process environment.

The admin GET DTO projects `request.app.state.config.ENABLE_LOGIN_FORM`, whose old source declaration already exists in `backend/open_webui/config.py`. The existing admin dependency remains attached. The admin POST form and setter have no `ENABLE_LOGIN_FORM` field or assignment from this change.

## Inputs and observed profile facts

The only supplied nonsecret current settings facts were `JWT_EXPIRES_IN=4w`, `ENABLE_SIGNUP=false`, and `ENABLE_LOGIN_FORM=true`. The validator computes `4w` as 2,419,200 seconds. It does not claim that the candidate currently uses these values or that the full current config has been migrated.

The historical `old-expiry-preservation-notes.md` contains a later primary correction: an earlier handoff incorrectly reported `ENABLE_PERSISTENT_CONFIG=False` for the imported/reopen runner. The actual runner uses `True`; the false value was from a separate performance harness. With persistent config enabled, the database value can override the environment value at startup. This validator remains stateless and cannot alone guarantee that the runtime uses its output. The primary is assessing a safe integration path that changes only the allowlisted setting without turning off all persistent config or broadening writes. The task is not a durable policy or default decision.

## Local checks

Run the named validator and DTO tests with:

```sh
PYTHONPYCACHEPREFIX=/tmp/bsd-epochs-pycache python3 validation/test_auth_settings.py
```

The suite covers exact success projection and hash, duplicate keys, schema/type refusals, positive strict expiry bounds including `4w`, malformed/unsupported/nonpositive/overflow duration, invalid UTF-8/JSON/constants, missing/directory/symlink/oversized files, JSON-only CLI success/refusal and in-process environment nonmutation, plus the admin GET field for both boolean values and no POST field. These are source-local stdlib and extracted-route tests only. Primary owns actual Hardy CLI integration, persistent-config interaction, password/session behavior, and restart checks.

The root task's accepted limits remain the one-shot offline runner and 12-request profile cap. No request, app, or runtime was run by this helper. The application behavior still requires primary verification. No full config, auth rows, token, key, or credentials were read or exported. The remaining gaps include other persistent settings, unknown source config, JWT purpose/claim shape, MFA, non-SQLite persistence, and any durable auth policy decision.
