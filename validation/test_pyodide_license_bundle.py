import hashlib
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
EXPECTED = {
    "PYODIDE-0.27.3-LICENSE.txt": "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5",
    "PYODIDE-JS-APACHE-2.0-LICENSE.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    "PYODIDE-0.27.3-SOURCE.txt": "de0695eeb599bb6a99d65284499cc6385bd6372624a43d16f75be8f8ae7fe8a6",
    "CPYTHON-3.12.7-LICENSE.txt": "3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf",
    "OPENBLAS-0.3.26-LICENSE.txt": "190b5a9c8d9723fe958ad33916bd7346d96fab3c5ea90832bb02d854f620fcff",
}


class PyodideLicenseBundleTests(unittest.TestCase):
    def test_exact_documents_and_build_copy_contract(self):
        prepare = (ROOT / "scripts" / "prepare-pyodide.js").read_text()
        for filename, expected_hash in EXPECTED.items():
            path = ROOT / "licenses" / "third-party" / filename
            self.assertTrue(path.is_file(), filename)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)
            self.assertIn(filename, prepare)
            self.assertIn(expected_hash, prepare)
        self.assertIn("static/pyodide/licenses", prepare)
        self.assertIn("copyPyodideLicenseBundle", prepare)

    def test_notice_preserves_upstream_license_scopes_and_source_identity(self):
        notice = (ROOT / "NOTICE").read_text()
        self.assertIn("overall project under MPL-2.0", notice)
        self.assertIn("JavaScript package under Apache-2.0", notice)
        self.assertIn("584d055066f1a94bae719a7e5752c314c04e7858", notice)
        self.assertIn("/pyodide/licenses/", notice)

    def test_source_notice_is_immutable_and_does_not_relicense_components(self):
        source_notice = (
            ROOT / "licenses" / "third-party" / "PYODIDE-0.27.3-SOURCE.txt"
        ).read_text()
        self.assertIn("584d055066f1a94bae719a7e5752c314c04e7858", source_notice)
        self.assertIn("archive/584d055066f1a94bae719a7e5752c314c04e7858.tar.gz", source_notice)
        self.assertIn("OpenAtelierUI itself remains licensed under", source_notice)
        self.assertIn("every embedded dependency retain their own licenses", source_notice)

    def test_frontend_notice_is_built_as_a_public_static_resource(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        public_path = "static/legal/THIRD_PARTY_NOTICES.frontend.txt"
        built_path = "/app/build/legal/THIRD_PARTY_NOTICES.frontend.txt"
        self.assertIn(public_path, dockerfile)
        self.assertIn(built_path, dockerfile)
        self.assertLess(dockerfile.index(public_path), dockerfile.index("npm run build"))


if __name__ == "__main__":
    unittest.main()
