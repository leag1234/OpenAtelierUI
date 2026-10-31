# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Response policy for rendering untrusted uploaded HTML as a static preview."""

UPLOADED_HTML_CONTENT_SECURITY_POLICY = (
    "sandbox; default-src 'none'; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)


def uploaded_html_response_headers() -> dict[str, str]:
    """Return response headers that isolate uploaded HTML from the app origin."""
    return {
        "Content-Security-Policy": UPLOADED_HTML_CONTENT_SECURITY_POLICY,
        "X-Content-Type-Options": "nosniff",
    }


def configured_security_headers_for_response(
    response_headers, configured_headers: dict[str, str]
) -> dict[str, str]:
    """Enforce route and global CSPs together while applying global headers."""
    headers = dict(configured_headers)
    route_csp = response_headers.get("Content-Security-Policy")
    configured_csp = headers.get("Content-Security-Policy")
    if route_csp and configured_csp:
        # Comma-separated policies are independently enforced, so both apply.
        headers["Content-Security-Policy"] = route_csp + ", " + configured_csp
    if (
        str(response_headers.get("X-Content-Type-Options", "")).lower() == "nosniff"
        and str(headers.get("X-Content-Type-Options", "")).lower() != "nosniff"
    ):
        headers["X-Content-Type-Options"] = "nosniff"
    return headers
