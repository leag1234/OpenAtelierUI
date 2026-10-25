# No-op plugin load preservation

The old tool and function loaders previously called their content-update store method after every stored-source load, even when `replace_imports` returned the exact source already stored. Those store methods also refresh `updated_at`, so merely loading an unchanged plugin made it appear edited. The loader now persists content only when the rewritten string differs from the stored string. A real legacy-import rewrite still uses the existing update path once; explicitly supplied source still avoids fetching or updating stored source. Function execution failures still remove the temporary module, deactivate the function through the existing update path, and remove the temporary file.

`test_plugin_load_preservation.py` parses and executes the actual checked-in loader function bodies with harmless synthetic tool/function source and in-memory stores. It checks identical-source no-op loads for both loader types, one persistence call for changed legacy imports, the explicit-source path, and failed-function cleanup/deactivation. Run it from `validation/`:

```sh
python3 -m unittest -v test_plugin_load_preservation.py
```

This source-isolated test does not prove database timestamp behavior, current-runtime continuation, or full plugin compatibility. The primary-owned frozen runtime comparison must verify stored source/settings/timestamps and useful continuation; the existing first failed row comparison remains part of that review. No performance or hardware-energy claim is made from skipping the known no-op store calls.
