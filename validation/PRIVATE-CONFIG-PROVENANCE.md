# Private configuration route correction provenance

This narrow BSD source change implements two measured current-version behaviors. The direct tool valves route requires the tool owner or an administrator and returns the existing 401 refusal to every other verified user before reading stored valves. The direct model route retains existing model visibility checks, but replaces `params` with an empty response-only object for a non-owner, non-admin reader. Owners and administrators retain the exact stored parameter values.

The model adjustment only changes the response dictionary returned by `model_dump`; it does not write to the model row or change invocation settings. The valves route still requires the existing verified-user dependency. The changes do not alter list, catalog, spec, update, or invocation paths. Their compatibility and confidentiality behavior beyond these two endpoints is not qualified here.

The source-isolated regression suite extracts the checked-in route bodies and runs them with small in-memory model/tool stubs, avoiding application startup or dependencies. It checks owner/admin values, member/unrelated denials, no valves accessor call before denial, reader response redaction, unchanged stored parameters, and existing private model denial. Run it from `validation/`:

```sh
python3 -m unittest -v test_private_config_routes.py
```

The separate importer suite remains covered by `python3 -m unittest -v test_migrate_sqlite.py`. These tests do not replace the primary-owned pinned-runtime HTTP replay or establish complete plugin/model confidentiality, visual UX, application startup, or production security certification. Anonymous authentication behavior remains on the existing `get_verified_user` dependency and is not changed by this patch.
