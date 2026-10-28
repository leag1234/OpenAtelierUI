# Collapsed sidebar geometry

The application shell now lets its main column use the available width after the existing 4.5-rem app rail, instead of subtracting the rail width a second time. The existing 260-pixel navigation panel remains unchanged when expanded. When collapsed, its navigation contents are marked inert and hidden from assistive technology. The existing rail shows a labeled, keyboard-operable Open navigation button that expands the panel.

The local checks assert the shell sizing contract, expanded rail/panel dimensions, collapsed inert state, and reopen control. They do not prove rendered pixel geometry or viewport behavior. Primary owns the actual 1600-pixel desktop and narrow-viewport review, including checking that collapsed main content reclaims the blank gutter and the expanded sidebar/routes remain usable.
