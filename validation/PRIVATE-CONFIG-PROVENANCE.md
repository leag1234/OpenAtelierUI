# Private configuration route correction provenance

This narrow BSD source change implements measured current-version behavior on five endpoint paths. The direct tool valves and valves/spec routes require the tool owner or an administrator and return the existing 401 refusal to every other verified user before reading stored valves, probing the tool-module cache, loading a module, or constructing its schema. The direct model route retains existing model visibility checks, but replaces `params` with an empty response-only object for a non-owner, non-admin reader. Owners and administrators retain the exact stored parameter values. The shared `/api/models` and `/api/v1/models` discovery callable deep-copies each cached model for the response and replaces `info.params` with `{}` for every role, without mutating cached parameters used by later calls or invocation.

The direct model adjustment only changes the response dictionary returned by `model_dump`; it does not write to the model row or change invocation settings. Discovery similarly changes only a deep-copied response object. The tool routes still require the existing verified-user dependency. The changes do not redesign workspace slash routes, catalog metadata, updates, or invocation. Their compatibility and confidentiality behavior beyond these measured paths is not qualified here.

Source-isolated regression suites extract the checked-in route bodies and run them with small in-memory model/tool stubs, avoiding application startup or dependencies. They check owner/admin values, member/unrelated denials, no valves accessor/cache/module/schema access before denial, direct-model response redaction, unchanged stored parameters, discovery response redaction for owner/member/admin, nested response-copy isolation, unchanged cached invocation parameters, and unchanged route aliases/positional signature. Run them from `validation/`:

```sh
python3 -m unittest -v test_private_config_routes.py
python3 ../backend/open_webui/test/test_model_discovery_alias.py
```

The separate importer suite remains covered by `python3 -m unittest -v test_migrate_sqlite.py`. These tests do not replace the primary-owned pinned-runtime HTTP replay or establish complete plugin/model confidentiality, visual UX, application startup, or production security certification. Anonymous authentication behavior remains on the existing `get_verified_user` dependency and is not changed by this patch.
