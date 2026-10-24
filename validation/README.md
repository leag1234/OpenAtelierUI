# Independent compatibility validation

The client and synthetic fixtures are independently authored BSD-3-Clause tests. The third-party Input Inspector is unmodified MIT software; its original notice is beside it. No newer Open WebUI implementation or tests are imported.

The parent laboratory controller runs this same client against frozen old/current container artifacts with synthetic data and no network. The current candidate still contains the original application, not the proposed compatibility changes. See FIRST-SLICE.md for measured requirements.

A standalone client invocation inside a correctly isolated, already running synthetic instance is:

```sh
python3 validation/clients/scenario.py --url http://127.0.0.1:8080 --output /tmp/fresh-synthetic-result.json --third-party validation/fixtures/third-party/input_inspector.py
```

This creates synthetic accounts, functions, a branched chat and a file; never aim it at an ordinary installation or private data. The client output is evidence to inspect, not a blanket pass declaration: HTTP200 can contain plugin errors.
