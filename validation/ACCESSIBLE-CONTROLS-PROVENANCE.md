# Accessible names for chat and settings controls

Explicit accessible names were added to the composer in both plain-text and rich-text modes, the send button, the observed response action buttons (edit, copy, read aloud, positive/negative feedback, continue, and regenerate), the theme and language selectors, and the settings close button. RichTextInput now accepts an optional `ariaLabel` and adds it to the editor only when provided, so other callers retain their existing markup. Event handlers, visual placement, request behavior, and plugin interfaces were not changed.

Focused source checks: `python3 -m unittest validation.test_accessible_controls -v`; `git diff --check`. They confirm the specified controls have labels in source; they do not establish accessibility-tree output or keyboard usability. Primary owns fresh compilation and rendered desktop/narrow keyboard review. No claim of accessibility certification or human acceptance is made.
