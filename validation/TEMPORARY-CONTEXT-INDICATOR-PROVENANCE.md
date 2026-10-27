# Temporary chat context indicator provenance

The source76 correction keeps the existing translated “Temporary Chat” label visible in the Navbar's model/title area while the existing temporary-chat store is enabled. It adds no new translation key, identifier, toast, modal, API call, or privacy promise; saved-chat headers do not display the badge.

`validation/test_temporary_context_indicator.py` checks that the conditional label uses the existing store and translation and is placed between the model selector and chat actions. This is a source check only. The primary owns offline compilation and rendered desktop/mobile review for spacing, legibility, and overlap. No claim of build success or human UX acceptance is made here.
