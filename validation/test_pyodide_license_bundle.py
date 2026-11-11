import hashlib
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
EXPECTED = {
    "PYODIDE-0.27.3-LICENSE.txt": "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5",
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

    def test_notice_keeps_upstream_metadata_discrepancy_visible(self):
        notice = (ROOT / "NOTICE").read_text()
        self.assertIn("Pyodide npm package declares Apache-2.0", notice)
        self.assertIn("official Pyodide repository tag carries MPL-2.0", notice)
        self.assertIn("/pyodide/licenses/", notice)

    def test_frontend_notice_is_built_as_a_public_static_resource(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        public_path = "static/legal/THIRD_PARTY_NOTICES.frontend.txt"
        built_path = "/app/build/legal/THIRD_PARTY_NOTICES.frontend.txt"
        self.assertIn(public_path, dockerfile)
        self.assertIn(built_path, dockerfile)
        self.assertLess(dockerfile.index(public_path), dockerfile.index("npm run build"))


if __name__ == "__main__":
    unittest.main()
