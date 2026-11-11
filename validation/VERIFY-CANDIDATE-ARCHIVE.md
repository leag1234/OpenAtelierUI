# Offline candidate archive verification

`verify_candidate_archive.py` verifies one gzip-compressed archive produced by
`docker save` without loading the image or extracting its filesystem. It is a
release and consumer-test preflight, not a substitute for runtime tests or
human legal review.

Run it from an exact, clean Git source checkout. Git must be available locally;
the command does not contact a remote:

```sh
python3 -B validation/verify_candidate_archive.py \
  --archive /absolute/path/openatelierui-candidate.tar.gz \
  --source-root "$PWD" \
  --expected-revision 0123456789abcdef0123456789abcdef01234567 \
  --output /absolute/path/candidate-verification.json
```

The archive, source root and optional output must use absolute paths. The
output file must not already exist and is created with mode `0600`.

The verifier binds:

- compressed archive size and SHA-256;
- the expected revision against a clean source checkout before and after scan;
- image configuration digest and exact 40-character revision/version labels;
- every streamed layer against its configured uncompressed RootFS diff ID;
- final overlay state after whiteout processing;
- project license, notice and provenance bytes against the source checkout;
- migration and rollback implementation/wrapper bytes and executable modes;
- the three pinned browser-Python license files;
- non-empty generated frontend notices, migration template and built UI;
- the packaged backend entrypoint source.

It refuses multi-image archives, malformed or ambiguous paths, duplicate outer
members, unsafe required files, missing artifacts, source mismatches, layer or
configuration digest mismatches, resource-bound violations and archives that
change while being scanned. It never invokes Docker, starts the application,
changes a data root or contacts a network service.

Successful verification only establishes archive identity and packaging
contents. Runtime behavior, dependency obligations, publication, deployment
and consumer migration remain separate gates.
