# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 26 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound26ContractTests(unittest.TestCase):
    def test_panel_tabs_remain_mounted_while_navbar_duplicate_is_stateful(self) -> None:
        navbar = source("src/lib/components/chat/Navbar.svelte")
        chat = source("src/lib/components/chat/Chat.svelte")
        self.assertIn("Boolean(history.currentId) ||", navbar)
        self.assertIn("!Array.isArray(selectedModels) ||", navbar)
        self.assertIn("selectedModels.filter((modelId)", navbar)
        self.assertNotIn("($chats?.length ?? 0) === 0", navbar)
        self.assertNotIn("{#if !nativeCompatibility || history.currentId}", chat)
        self.assertEqual(chat.count("<ChatControls"), 1)

    def test_regenerate_attributes_match_native_contract(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        native = response[response.index('class="hidden regenerate-response-button"') :]
        native = native[: native.index("{#if siblings.length > 1}")]
        hidden = native[: native.index("</button>")]
        self.assertNotIn("aria-label", hidden)
        self.assertIn("removeGeneratedId: true", native)
        self.assertIn("ariaHasPopup: 'true'", native)

    def test_native_profile_image_is_decorative_and_named(self) -> None:
        profile = source("src/lib/components/chat/Messages/ProfileImage.svelte")
        self.assertIn('alt="profile"', profile)
        self.assertIn("aria-hidden={currentCompatibility ? 'true' : undefined}", profile)

    def test_phone_toolbar_omits_streaming_and_stopped_states(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        self.assertIn("window.matchMedia('(min-width: 640px)')", chat)
        self.assertIn(
            "{#if nativeCompatibility && history.currentId && selectionToolbarDesktop}",
            chat,
        )

    def test_current_turn_delete_waits_for_terminal_state(self) -> None:
        user = source("src/lib/components/chat/Messages/UserMessage.svelte")
        self.assertIn("currentTurnUserId = history.messages[history.currentId]?.parentId", user)
        self.assertIn("history.messages[history.currentId]?.done === true", user)
        self.assertIn("aria-hidden={!v0113Compatibility ? 'true' : undefined}", user)
        self.assertIn("M9 5.5h6M10 3.5h4", user)

    def test_native_home_model_image_omits_alt_attribute(self) -> None:
        placeholder = source("src/lib/components/chat/Placeholder.svelte")
        self.assertIn("{...(v0113Compatibility ? { alt: '' } : {})}", placeholder)

    def test_stop_is_optimistic_but_rolls_back_uncertain_outcomes(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        stop = chat[chat.index("const stopResponse = async () => {") :]
        stop = stop[: stop.index("const submitMessage = async")]
        self.assertLess(stop.index("markCurrentResponsesDone(true"), stop.index("requestSavedChatStop("))
        self.assertIn("restoreResponseStates(responseStatesBeforeStop);", stop)
        self.assertIn("taskIds = remainingIds.length ? remainingIds : taskIdsBeforeStop;", stop)
        self.assertIn("outcome.state === 'stopped' || outcome.state === 'already-finished'", stop)
        self.assertIn("if (settledStop && optimisticallyStopped && savedChat)", stop)
        self.assertIn("restoreResponseStates(responseStatesBeforeStop, true);", stop)


if __name__ == "__main__":
    unittest.main()
