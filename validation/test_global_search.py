import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "backend/open_webui/routers/search.py"


def load_pure_helpers():
    tree = ast.parse(SOURCE_PATH.read_text())
    selected = []
    names = {
        "_tokens",
        "_matches",
        "_seconds",
        "_chat_messages",
        "_message_text",
        "_selected_message",
    }
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {"__future__", "typing"}:
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            selected.append(node)
    namespace = {}
    exec(compile(ast.Module(selected, type_ignores=[]), str(SOURCE_PATH), "exec"), namespace)
    return namespace


class GlobalSearchCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text()
        cls.helpers = load_pure_helpers()

    def test_current_contract_shape_and_resource_bounds_are_explicit(self):
        self.assertIn('SearchQueryKind = Literal["all", "chat", "message", "note"]', self.source)
        self.assertIn('SearchSharing = Literal["all", "mine", "shared"]', self.source)
        self.assertIn('PAGE_SIZE = 24', self.source)
        self.assertIn('MAX_DOMAIN_CANDIDATES = PAGE_SIZE * 100', self.source)
        self.assertIn('q: str = Query(default="", max_length=500)', self.source)
        self.assertIn('scope: str = Query(default="", max_length=200)', self.source)
        self.assertIn('page: int = Query(default=1, ge=1, le=100000)', self.source)
        self.assertIn("Chats.get_chats_by_folder_id_and_user_id(folder.id, folder.user_id)", self.source)
        self.assertIn("shared=not owned or bool(chat.share_id)", self.source)

    def test_query_matching_is_trimmed_casefolded_and_requires_every_token(self):
        tokens = self.helpers["_tokens"]("  Round50  AMBER ")
        self.assertEqual(tokens, ["round50", "amber"])
        self.assertTrue(self.helpers["_matches"]("Amber / ROUND50", tokens))
        self.assertFalse(self.helpers["_matches"]("Round50 only", tokens))

    def test_chat_projection_handles_history_and_selects_relevant_message(self):
        payload = {
            "history": {
                "messages": {
                    "first": {"id": "m1", "content": "plain opening"},
                    "second": {"id": "m2", "content": "Amber detail"},
                }
            }
        }
        messages = self.helpers["_chat_messages"](payload)
        selected = self.helpers["_selected_message"](messages, ["amber"])
        self.assertEqual(selected["id"], "m2")
        self.assertEqual(self.helpers["_selected_message"](messages, [])["id"], "m2")

    def test_nanosecond_timestamps_are_projected_to_epoch_seconds(self):
        seconds = 1_700_000_123
        self.assertEqual(self.helpers["_seconds"](seconds), seconds)
        self.assertEqual(self.helpers["_seconds"](seconds * 1_000_000_000), seconds)
        self.assertEqual(self.helpers["_seconds"](None), 0)

    def test_main_registers_the_router_at_the_measured_path(self):
        main = (ROOT / "backend/open_webui/main.py").read_text()
        self.assertIn("    search,", main)
        self.assertIn(
            'app.include_router(search.router, prefix="/api/v1/search", tags=["search"])',
            main,
        )


if __name__ == "__main__":
    unittest.main()
