# First-use welcome action

The welcome now presents the configured product name, a short explanation of setting up account access for conversations, files, and models, and one visible “Continue to account setup” button. The button contains the icon and label, so its full target activates the existing setup handler and its visible text supplies its accessible name. The inherited rotating slogans and detached “Get started” caption were removed. Existing logo selection and the existing signup/LDAP mode choice are retained.

While the welcome is visible, the underlying authentication panel is both `inert` and `aria-hidden`. Activating setup waits for Svelte’s DOM update and then focuses the first applicable name, LDAP username, or email field. The name and LDAP fields now use explicit labels, alongside the existing email and password labels. Signup, authentication, provider, and role logic were not changed.

`python3 -m unittest validation.test_first_use_welcome -v` passes two source-structure checks; `git diff --check` passes. These checks do not replace the primary-owned fresh compile, rendered desktop/narrow review, keyboard focus test, or first-account lifecycle. No browser or native runtime was used in this slice.
