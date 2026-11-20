from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "src/lib/components/chat/Chat.svelte").read_text()
CONTENT = (ROOT / "src/lib/components/chat/Messages/ContentRenderer.svelte").read_text()
LAYOUT = (ROOT / "src/routes/+layout.svelte").read_text()
MENU = (ROOT / "src/lib/components/layout/Navbar/Menu.svelte").read_text()
SELECTOR = (ROOT / "src/lib/components/chat/ModelSelector/Selector.svelte").read_text()
TOOLBAR = (ROOT / "src/lib/components/chat/CurrentSelectionToolbar.svelte").read_text()
NAVBAR = (ROOT / "src/lib/components/chat/Navbar.svelte").read_text()
SIDEBAR = (
    ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
).read_text()


class NativeTransientContractTests(unittest.TestCase):
    def test_native_model_trigger_has_no_empty_child(self) -> None:
        native = SELECTOR[SELECTOR.index("{#if nativeCompact}") : SELECTOR.index("{:else}", SELECTOR.index("{#if nativeCompact}"))]
        self.assertNotIn("<span></span>", native)

    def test_chat_menu_uses_a_dom_free_named_slot_wrapper(self) -> None:
        self.assertIn('<svelte:fragment slot="content">', MENU)
        self.assertNotIn('<div slot="content">', MENU)

    def test_native_markdown_identity_has_two_stable_uuid_shaped_segments(self) -> None:
        self.assertIn('id={currentCompatibility ? `${id}-${id}` : id}', CONTENT)

    def test_selection_toolbar_is_body_portalled_and_message_bound(self) -> None:
        self.assertIn("document.body.appendChild(node)", TOOLBAR)
        self.assertIn('id={`floating-buttons-${chatId}-${messageId}`}', TOOLBAR)
        self.assertIn("use:portalToBody", TOOLBAR)
        self.assertIn("chatId={$chatId}", CHAT)
        self.assertIn("messageId={history.currentId}", CHAT)

    def test_native_layout_retains_quiet_polite_announcer(self) -> None:
        self.assertIn("{#if $config?.compatibility_profile === 'native'}", LAYOUT)
        self.assertIn('<div aria-live="polite"></div>', LAYOUT)

    def test_native_sidebar_toggle_initializes_the_accessible_tooltip_contract(self) -> None:
        self.assertIn("import tippy from 'tippy.js';", NAVBAR)
        self.assertIn('content: $i18n.t(\'Open Sidebar\')', NAVBAR)
        self.assertIn('placement: \'bottom\'', NAVBAR)
        self.assertIn("const syncChat = (activeChatId: string | null)", NAVBAR)
        self.assertIn("if (!activeChatId)", NAVBAR)
        self.assertIn("chatId.subscribe(syncChat)", NAVBAR)
        self.assertIn('instance?.destroy();', NAVBAR)
        self.assertIn('instance = null;', NAVBAR)
        self.assertIn('instance.show();', NAVBAR)
        self.assertIn('instance.hide();', NAVBAR)
        self.assertIn('use:nativeSidebarTooltip', NAVBAR)

    def test_native_sidebar_shadow_retains_reference_attribute_contracts(self) -> None:
        self.assertGreaterEqual(SIDEBAR.count('<div id="" class="w-full">'), 2)
        self.assertIn('fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="size-4"', SIDEBAR)
        self.assertGreaterEqual(SIDEBAR.count('fill="currentColor" stroke="currentColor"'), 3)


if __name__ == "__main__":
    unittest.main()
