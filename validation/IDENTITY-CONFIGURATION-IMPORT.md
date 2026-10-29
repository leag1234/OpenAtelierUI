# Selected identity configuration import

The `--include-identity-configuration` option explicitly selects four recent key/value settings for a prepared copy. It is off by default.

| Source key | Prepared legacy target key | Supported values |
| --- | --- | --- |
| `ui.enable_signup` | `ui.enable_signup` | JSON boolean |
| `ui.enable_login_form` | `ui.ENABLE_LOGIN_FORM` | JSON boolean |
| `ui.default_user_role` | `ui.default_user_role` | `pending` or `user` |
| `auth.admin.show` | `auth.admin.show` | JSON boolean |

All four keys are required when the option is selected. Duplicate keys, documented environment/legacy aliases, parent/dotted overrides, malformed JSON and unsupported types or roles are refused. The uppercase target login key comes from the pinned old own configuration registry; it is not a copied newer implementation.

The option is available on the core preview/conversion commands and the existing preserved-copy coordinator. Preview names the selected domain and keeps the prototype limitation. A core preview returns exit code `3` when cutover remains ineligible, even when the four selected values are valid. A prepared partial copy is not a cutover approval.

MFA, OAuth, LDAP, trusted headers, cookie/session policy, secret-bearing identity-provider settings and other unselected rows remain archived and unqualified. Copying these four settings must not imply those protections were migrated. Read the complete preview before starting a copied instance.

The observed synthetic source had signup disabled, login enabled, default role `pending` and admin visibility enabled. The previous partial copy omitted these values and relied on its lab environment. The primary regression fixtures now verify exact selected target values, source/template/upload preservation, unsupported rows remaining unmapped, aliases/types/refusals, and actual CLI/coordinator propagation. A targeted runtime test with deliberately opposite environment settings is still required before claiming effective precedence.

Provenance: the original separated BSD helper supplied the parser and inspection mapping; after its execution reached an output limit, one fresh no-history Luna execution finished the public signatures and option wiring using only own BSD source and the normalized synthetic settings above. The primary reviewed and tested the result. No newer upstream implementation was supplied, and this is not independent legal clearance.
