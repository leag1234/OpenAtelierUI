from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativePaneShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.action = (ROOT / "src/lib/actions/nativePaneShellContract.ts").read_text()
        cls.chat = (ROOT / "src/lib/components/chat/Chat.svelte").read_text()

    def test_native_shell_removes_only_observed_paneforge_identity(self) -> None:
        for attribute in (
            "id",
            "data-direction",
            "data-pane-group",
            "data-pane-group-id",
            "data-pane",
            "data-pane-id",
        ):
            self.assertIn(f"'{attribute}'", self.action)
        self.assertNotIn("removeAttribute('class')", self.action)
        self.assertIn("new MutationObserver(reconcile)", self.action)

    def test_action_is_scoped_to_the_native_chat_container(self) -> None:
        self.assertIn(
            "import { nativePaneShellContract } from '$lib/actions/nativePaneShellContract';",
            self.chat,
        )
        self.assertIn("use:nativePaneShellContract={nativeCompatibility}", self.chat)
        self.assertIn("id=\"chat-container\"", self.chat)

    def test_native_main_pane_matches_observed_reference_attributes(self) -> None:
        self.assertIn("id={nativeCompatibility ? undefined : 'chat-pane'}", self.chat)
        self.assertIn("data-main-chat-pane={nativeCompatibility ? '' : undefined}", self.chat)
        self.assertNotIn("chat-panel-main-chat-view", self.chat)
        self.assertNotIn("data-panel-group={nativeCompatibility", self.chat)
        self.assertNotIn("aria-labelledby={nativeCompatibility ? 'chat-panel-main-chat'", self.chat)

    def test_inner_chat_pane_identity_remains_available(self) -> None:
        self.assertIn("id={nativeCompatibility ? 'chat-pane' : undefined}", self.chat)


if __name__ == "__main__":
    unittest.main()
