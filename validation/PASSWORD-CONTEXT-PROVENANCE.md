# Bounded password-session context provenance

This candidate slice implements the ordinary-password subset of `TASK-OPENATELIER-PASSWORD-CONTEXT-001`. Only after `Auths.authenticate_user` succeeds in the ordinary password branch, and only while the existing `OPENATELIER_SESSION_EPOCHS` opt-in is enabled, the route issues the measured session context. A single server-owned integer issuance second supplies `iat` and `auth_time`; `typ` is `session`, `auth_method` is `password`, and `mfa_verified` is false. False records that this password-only path did not verify an additional factor; the change adds no MFA claim of successful verification.

For a positive configured duration, `exp` is that same issue second plus the existing parsed duration. The returned `expires_at` and cookie expiry match `exp`. The ordinary generic token signer still adds the existing session stamp and fresh JTI and keeps its guarded expected-stamp behavior. The follow-up `/auths/` read returns the previously validated token and expiry unchanged, without a new issuance or database write.

Trusted-header and auth-disabled sign-in paths keep generic token issuance and receive no password/MFA labels. Failed password authentication issues no token. Default-off password sign-in keeps its prior generic token payload. The helper is not called by signup, LDAP, OAuth, API-key, or other generic issuance paths. No issuer key, algorithm, JWT default, schema, new dependency, login policy, or MFA enrollment behavior changed.

The supplied safe current protocol facts were limited to a successful synthetic ordinary password sign-in under `4w`: `typ=session`, `auth_method=password`, `mfa_verified=false`, integer `iat`/`auth_time`/`exp`, `iat=auth_time`, and `exp-iat=2,419,200` seconds; `/auths/` returned the same token. Those are bounded observations, not a statement that MFA was verified or that other authentication methods have the same metadata.

The source-bound suite runs with:

```sh
PYTHONPYCACHEPREFIX=/tmp/bsd-epochs-pycache python3 backend/open_webui/test/test_session_epoch_auth.py
```

It checks the successful ordinary-password branch's exact claim names/types and arithmetic with a fixed test clock, failed-password non-issuance, default-off behavior, trusted-header and auth-disabled label isolation, purpose rejection, and exact token/expiry preservation through session reads. These extracted-source tests use controlled JWT/SQLite doubles; primary owns the actual PyJWT signing, frozen current-protocol replay, and mounted runtime/restart verification. LDAP/OAuth/signup/browser/MFA and full auth compatibility remain outside the qualification.
