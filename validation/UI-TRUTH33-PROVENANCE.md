# First-use Notice and Composer Label

The onboarding notice now explains that available models and services depend on server configuration and that requests may be sent to configured external providers. It directs users to provider settings before sharing sensitive information. The notice makes no local-only or security guarantee.

The chat composer already receives a label translated through the current locale. The rich-text editor now updates its existing DOM label when that prop changes; it does not recreate the editor or replace its content.

`test_ui_truth33.py` checks the authored notice and the reactive label-update path in source. It is not a Svelte compile or rendered accessibility test. A fresh frontend build and rendered locale-change review remain with primary integration.
