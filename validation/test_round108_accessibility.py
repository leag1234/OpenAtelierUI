import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Round108AccessibilityContract(unittest.TestCase):
    def test_viewport_does_not_disable_zoom(self):
        source = (ROOT / "src/app.html").read_text()
        self.assertIn("width=device-width, initial-scale=1, viewport-fit=cover", source)
        self.assertNotIn("maximum-scale=1", source)
        self.assertNotIn("user-scalable=no", source)

    def test_current_chat_tab_owns_only_tab_roles_and_controls_real_container(self):
        source = (ROOT / "src/lib/components/chat/ChatControls.svelte").read_text()
        self.assertIn('role="tablist" aria-label="Tabs" tabindex="-1"', source)
        self.assertIn('aria-controls="chat-container"', source)
        self.assertNotIn('aria-controls="chat-panel-main-chat-view"', source)
        self.assertNotIn('aria-label="Close tab"', source)

    def test_saved_chat_reconnect_reconciles_orphaned_pending_responses(self):
        source = (ROOT / "src/lib/components/chat/Chat.svelte").read_text()
        self.assertIn("const pendingReplyIds = Object.entries(history.messages)", source)
        self.assertIn("await getTaskIdsByChatId(localStorage.token, currentChatId)", source)
        self.assertIn("messages[messageId] = { ...message, done: true }", source)
        self.assertIn("taskIds = null", source)
        self.assertIn("history = { ...history, messages }", source)
        self.assertIn("if (!activeTasks.length)", source)
        self.assertIn("for (let attempt = 0; attempt < 30; attempt += 1)", source)
        self.assertIn("reconciliationEpoch !== reconnectReconciliationEpoch", source)
        self.assertIn("history = { ...history, messages: { ...history.messages } }", source)


if __name__ == "__main__":
    unittest.main()
