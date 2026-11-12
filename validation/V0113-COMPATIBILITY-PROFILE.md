# Open WebUI v0.11.3 compatibility profile

OpenAtelierUI exposes the active integration profile as `compatibility_profile` in `/api/config`. The validation candidate selects `open-webui-v0.11.3` by default. Operators can select the native presentation explicitly with:

```sh
OPENATELIER_COMPATIBILITY_PROFILE=native
```

The v0.11.3 profile is an integration contract rather than a product-name or consumer-specific adapter. It covers the observable message and navigation hooks used by existing overlays:

- `#message-<id>` remains the outer `group` element;
- the single `.chat-assistant` hook is an inner descendant;
- assistant content retains an inner `.markdown-prose` wrapper;
- timestamps and secondary actions retain `.hover-reveal`, with keyboard-focus and touch access;
- assistant avatars hide below the large breakpoint and response names retain `response-message-<id>`;
- the active conversation link exposes `.selected`;
- chat and folder headings, folder creation, branding order, and the compact legacy header remain stable.

`ENABLE_NOTES=False` disables the optional Notes router and its advertised sidebar feature. `OPENATELIER_PRIVATE_NOTES_COMPAT=0` remains an explicit independent rollback switch. Either false value disables Notes.

The native profile currently re-enables the additional temporary-chat and controls presentation. Compatible message hooks are additive and remain available in native mode. This validation profile does not establish a durable release default; that decision remains subject to the project's architecture and release review.
