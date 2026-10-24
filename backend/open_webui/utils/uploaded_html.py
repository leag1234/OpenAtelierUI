# SPDX-License-Identifier: BSD-3-Clause
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
    """Keep a route's explicit CSP while applying the other global headers."""
    headers = dict(configured_headers)
    if "Content-Security-Policy" in response_headers:
        headers.pop("Content-Security-Policy", None)
    return headers
