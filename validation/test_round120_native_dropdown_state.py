from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeDropdownStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.action = (ROOT / "src/lib/actions/nativeMenuTriggerContract.ts").read_text()
        cls.message_input = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text()
        cls.navbar = (ROOT / "src/lib/components/chat/Navbar.svelte").read_text()
        cls.selector = (ROOT / "src/lib/components/chat/ModelSelector/Selector.svelte").read_text()
        cls.selection = (ROOT / "src/lib/components/chat/CurrentSelectionToolbar.svelte").read_text()

    def test_native_menu_contract_removes_only_observed_framework_state(self) -> None:
        for attribute in (
            "aria-controls",
            "data-melt-dropdown-menu-trigger",
            "data-menu-trigger",
            "data-state",
            "tabindex",
        ):
            self.assertIn(f"'{attribute}'", self.action)
        self.assertIn("node.setAttribute('aria-haspopup', 'menu')", self.action)
        self.assertIn("new MutationObserver(reconcile)", self.action)

    def test_menu_actions_keep_framework_keyboard_action(self) -> None:
        self.assertIn("use:builder.action", self.message_input)
        self.assertIn("use:nativeMenuTriggerContract={!v0113Compatibility}", self.message_input)
        self.assertIn("use:builder.action use:nativeMenuTriggerContract={true}", self.navbar)
        self.assertIn("nativeMenuTriggerContract(trigger, true).destroy", self.selector)

    def test_reference_attribute_contract_is_present_on_adjacent_icons(self) -> None:
        self.assertEqual(
            self.selection.count(
                'aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none"'
            ),
            2,
        )
        self.assertIn(
            'aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" class="size-[1.125rem]"',
            self.message_input,
        )
        self.assertIn(
            '<path stroke-linecap="round" stroke-linejoin="round" d="M5 11a7 7 0 0 0 14 0M12 18v3" />',
            self.message_input,
        )

    def test_native_phone_buttons_do_not_add_unobserved_type(self) -> None:
        self.assertNotIn('<button type="button" class="flex size-6 {$showSidebar', self.navbar)
        self.assertNotIn('<button type="button" id="temporary-chat-button"', self.navbar)
        self.assertNotIn(
            '<button type="button" class="flex size-6 cursor-pointer items-center justify-center rounded-lg',
            self.navbar,
        )


if __name__ == "__main__":
    unittest.main()
