# SPDX-License-Identifier: BSD-3-Clause
"""Exercise the actual env.py device block with synthetic platforms/modules."""

import ast
import pathlib
import types
import unittest


ENV_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "env.py"


class FakeTorch:
    def __init__(self, cuda=False, mps_available=False, mps_built=False, error=None):
        self.cuda = types.SimpleNamespace(is_available=lambda: cuda)
        self.mps_calls = []

        def mps_available_call():
            self.mps_calls.append("available")
            if error:
                raise error
            return mps_available

        def mps_built_call():
            self.mps_calls.append("built")
            return mps_built

        self.backends = types.SimpleNamespace(
            mps=types.SimpleNamespace(
                is_available=mps_available_call,
                is_built=mps_built_call,
            )
        )


class FakeImporter:
    def __init__(self, module=None, error=None, installed=True):
        self.module = module
        self.error = error
        self.calls = []
        self.util = types.SimpleNamespace(
            find_spec=lambda name: object() if name == "torch" and installed else None
        )

    def import_module(self, name):
        self.calls.append(name)
        if self.error:
            raise self.error
        return self.module


def _selected_env_nodes():
    tree = ast.parse(ENV_SOURCE.read_text(encoding="utf-8"), filename=str(ENV_SOURCE))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "_load_torch",
            "__getattr__",
            "__dir__",
            "_torch_is_installed",
        }:
            selected.append(node)
        elif isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names == {"USE_CUDA"} or names == {"__all__"}:
                selected.append(node)
        elif isinstance(node, ast.If):
            expression = ast.unparse(node.test)
            if expression == "USE_CUDA.lower() == 'true'" or expression == "sys.platform == 'darwin'":
                selected.append(node)
            elif "'torch' not in __all__" in expression:
                selected.append(node)
    expected = {
        "USE_CUDA", "cuda-branch", "mps-branch", "_load_torch", "__getattr__", "__dir__",
        "_torch_is_installed", "__all__"
    }
    observed = set()
    for node in selected:
        if isinstance(node, ast.Assign):
            observed.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.FunctionDef):
            observed.add(node.name)
        elif isinstance(node, ast.If):
            if ast.unparse(node.test) == "USE_CUDA.lower() == 'true'":
                observed.add("cuda-branch")
            elif ast.unparse(node.test) == "sys.platform == 'darwin'":
                observed.add("mps-branch")
    if not expected <= observed:
        raise AssertionError(f"env source block extraction is incomplete: {expected - observed}")
    return selected


def load_env_block(
    platform, use_cuda, torch_module=None, import_error=None, torch_installed=True
):
    variables = {
        "__name__": "synthetic_open_webui_env",
        "os": types.SimpleNamespace(environ={"USE_CUDA_DOCKER": use_cuda}),
        "sys": types.SimpleNamespace(platform=platform),
        "importlib": FakeImporter(torch_module, import_error, torch_installed),
    }
    exec(
        compile(ast.Module(body=_selected_env_nodes(), type_ignores=[]), str(ENV_SOURCE), "exec"),
        variables,
    )
    return variables


class EnvTorchImportTests(unittest.TestCase):
    def test_linux_and_windows_cpu_do_not_import_torch_eagerly(self):
        for platform in ("linux", "win32"):
            with self.subTest(platform=platform):
                torch = FakeTorch(mps_available=True, mps_built=True)
                env = load_env_block(platform, "false", torch)
                self.assertEqual(env["DEVICE_TYPE"], "cpu")
                self.assertEqual(env["importlib"].calls, [])
                self.assertNotIn("torch", env)

    def test_macos_mps_available_unavailable_and_error_paths(self):
        available = load_env_block(
            "darwin", "false", FakeTorch(mps_available=True, mps_built=True)
        )
        self.assertEqual(available["DEVICE_TYPE"], "mps")
        self.assertEqual(available["importlib"].calls, ["torch"])

        unavailable = load_env_block(
            "darwin", "false", FakeTorch(mps_available=False, mps_built=True)
        )
        self.assertEqual(unavailable["DEVICE_TYPE"], "cpu")

        failed = load_env_block(
            "darwin", "false", FakeTorch(error=RuntimeError("synthetic MPS failure"))
        )
        self.assertEqual(failed["DEVICE_TYPE"], "cpu")

    def test_cuda_selection_and_failure_fallback_are_preserved(self):
        available = load_env_block("linux", "true", FakeTorch(cuda=True))
        self.assertEqual(available["DEVICE_TYPE"], "cuda")
        self.assertEqual(available["importlib"].calls, ["torch"])

        unavailable = load_env_block("linux", "true", FakeTorch(cuda=False))
        self.assertEqual(unavailable["DEVICE_TYPE"], "cpu")
        self.assertEqual(unavailable["os"].environ["USE_CUDA_DOCKER"], "false")
        self.assertEqual(unavailable["USE_CUDA"], "false")

        failed_import = load_env_block(
            "linux", "true", import_error=ImportError("synthetic missing torch")
        )
        self.assertEqual(failed_import["DEVICE_TYPE"], "cpu")
        self.assertEqual(failed_import["USE_CUDA"], "false")

    def test_explicit_and_wildcard_exports_resolve_the_actual_module(self):
        torch = FakeTorch()
        env = load_env_block("linux", "false", torch)
        self.assertIn("torch", env["__all__"])
        self.assertIn("torch", env["__dir__"]())
        self.assertEqual(env["importlib"].calls, [])
        self.assertIs(env["__getattr__"]("torch"), torch)
        self.assertEqual(env["importlib"].calls, ["torch"])
        self.assertIs(env["torch"], torch)

        existing = FakeTorch(cuda=True)
        imported = load_env_block("linux", "true", existing)
        self.assertIs(imported["__getattr__"]("torch"), existing)
        self.assertEqual(imported["importlib"].calls, ["torch"])

        absent = load_env_block("linux", "false", torch_installed=False)
        self.assertNotIn("torch", absent["__all__"])
        self.assertNotIn("torch", absent["__dir__"]())


if __name__ == "__main__":
    unittest.main(verbosity=2)
