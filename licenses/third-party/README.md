# Version-pinned browser runtime licenses

These verbatim license texts accompany the browser Python artifacts generated
by `scripts/prepare-pyodide.js`. The build verifies every file hash before
copying the texts to `/pyodide/licenses/`.

| Artifact | Official source | SHA-256 |
| --- | --- | --- |
| Pyodide 0.27.3 | `https://github.com/pyodide/pyodide/blob/0.27.3/LICENSE` | `1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5` |
| Pyodide JavaScript package | `https://github.com/pyodide/pyodide/blob/0.27.3/src/js/package.json` and `https://www.apache.org/licenses/LICENSE-2.0.txt` | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| Pyodide exact source notice | tag `0.27.3`, commit `584d055066f1a94bae719a7e5752c314c04e7858` | `de0695eeb599bb6a99d65284499cc6385bd6372624a43d16f75be8f8ae7fe8a6` |
| CPython 3.12.7 | `https://github.com/python/cpython/blob/v3.12.7/LICENSE` | `3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf` |
| OpenBLAS 0.3.26 | `https://github.com/OpenMathLib/OpenBLAS/blob/v0.3.26/LICENSE` | `190b5a9c8d9723fe958ad33916bd7346d96fab3c5ea90832bb02d854f620fcff` |

The exact source tag declares the overall Pyodide project under MPL-2.0 and
declares its JavaScript package under Apache-2.0. The build preserves both
scopes, the immutable source identity and the licenses of embedded components;
it does not relicense any third-party component.
