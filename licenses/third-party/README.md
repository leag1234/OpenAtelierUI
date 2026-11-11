# Version-pinned browser runtime licenses

These verbatim license texts accompany the browser Python artifacts generated
by `scripts/prepare-pyodide.js`. The build verifies every file hash before
copying the texts to `/pyodide/licenses/`.

| Artifact | Official source | SHA-256 |
| --- | --- | --- |
| Pyodide 0.27.3 | `https://github.com/pyodide/pyodide/blob/0.27.3/LICENSE` | `1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5` |
| CPython 3.12.7 | `https://github.com/python/cpython/blob/v3.12.7/LICENSE` | `3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf` |
| OpenBLAS 0.3.26 | `https://github.com/OpenMathLib/OpenBLAS/blob/v0.3.26/LICENSE` | `190b5a9c8d9723fe958ad33916bd7346d96fab3c5ea90832bb02d854f620fcff` |

The Pyodide npm package metadata in the exact build declares Apache-2.0, while
the official repository tag above carries MPL-2.0. This directory preserves
the official tagged license text and records the discrepancy; it does not make
a legal interpretation or relicense any third-party component.
