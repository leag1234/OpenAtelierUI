from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeSidebarIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.action = (ROOT / "src/lib/actions/nativeMenuTriggerContract.ts").read_text()
        cls.sidebar = (
            ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
        ).read_text()

    def test_account_trigger_preserves_builder_behavior_but_removes_generated_identity(self) -> None:
        self.assertIn("use:builder.action", self.sidebar)
        self.assertIn(
            "use:nativeMenuTriggerContract={{ enabled: true, removeGeneratedId: true }}",
            self.sidebar,
        )
        self.assertIn("removeGeneratedId && node.hasAttribute('id')", self.action)
        self.assertIn("new MutationObserver(reconcile)", self.action)

    def test_folder_content_identity_is_on_the_observed_inner_container(self) -> None:
        self.assertIn(
            '<div><div {...contentSlotAttribute} id="sidebar-folders-content" class="w-full px-1.5">',
            self.sidebar,
        )
        self.assertNotIn(
            '<div id="sidebar-folders-content"><div class="w-full px-1.5">',
            self.sidebar,
        )

    def test_profile_image_keeps_observed_accessible_name_attributes(self) -> None:
        self.assertIn("alt={$i18n.t('Open User Profile Menu')}", self.sidebar)
        self.assertIn("aria-label={$i18n.t('Open User Profile Menu')}", self.sidebar)


if __name__ == "__main__":
    unittest.main()
