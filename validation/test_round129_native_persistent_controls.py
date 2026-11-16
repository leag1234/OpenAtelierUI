from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativePersistentControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.chat = (ROOT / "src/lib/components/chat/Chat.svelte").read_text()
        cls.message_input = (
            ROOT / "src/lib/components/chat/MessageInput.svelte"
        ).read_text()
        cls.code_editor = (
            ROOT / "src/lib/components/common/CodeEditor.svelte"
        ).read_text()

    def test_native_chat_keeps_hidden_new_chat_fallback(self) -> None:
        marker = 'id="new-chat-button"'
        start = self.chat.rfind("{#if nativeCompatibility}", 0, self.chat.index(marker))
        end = self.chat.index("{/if}", self.chat.index(marker))
        control = self.chat[start:end]
        self.assertIn('class="hidden"', control)
        self.assertIn("aria-label={$i18n.t('New Chat')}", control)
        self.assertIn("on:click={initNewChat}", control)

    def test_voice_recorder_stays_mounted_and_hidden_when_idle(self) -> None:
        wrapper = '<div class:hidden={!recording}>'
        start = self.message_input.index(wrapper)
        end = self.message_input.index("</div>", start)
        self.assertIn("<VoiceRecording", self.message_input[start:end])
        self.assertIn("{:else}", self.message_input[start - 80 : start])
        self.assertIn("{#if !recording}", self.message_input[end : end + 140])

    def test_v0113_voice_recorder_remains_conditional(self) -> None:
        wrapper = self.message_input.index('<div class:hidden={!recording}>')
        start = self.message_input.rfind("{#if v0113Compatibility}", 0, wrapper)
        end = self.message_input.index("{:else}", start)
        legacy = self.message_input[start:end]
        self.assertIn("{#if recording}", legacy)
        self.assertNotIn('<div class:hidden={!recording}>', legacy)

    def test_native_code_editor_restores_observed_accessibility_fields(self) -> None:
        marker = "const content = editorRoot?.querySelector('.cm-content');"
        start = self.code_editor.index(marker)
        end = self.code_editor.index("editorRoot?.querySelectorAll", start)
        block = self.code_editor[start:end]
        self.assertIn("content.setAttribute('aria-label', 'Code Editor')", block)
        self.assertIn(
            "content.setAttribute('aria-placeholder', 'Enter your code here...')",
            block,
        )
        self.assertIn("content.setAttribute('tabindex', '0')", block)
        self.assertIn("content.setAttribute('writingsuggestions', 'false')", block)


if __name__ == "__main__":
    unittest.main()
