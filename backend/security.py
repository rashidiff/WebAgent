from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


BLOCKED_SCHEMES = {"javascript", "data", "file", "blob", "about", "chrome", "chrome-extension"}


def validate_navigation_url(url: str, allow_private_networks: bool = False) -> tuple[bool, str]:
    """Validate a navigation target before it reaches the browser executor."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False, "URL could not be parsed."

    if parsed.scheme.lower() in BLOCKED_SCHEMES or parsed.scheme.lower() not in {"http", "https"}:
        return False, "Only http(s) navigation is allowed."
    if not parsed.hostname:
        return False, "Navigation URL must include a hostname."
    if parsed.username or parsed.password:
        return False, "URLs containing embedded credentials are blocked."

    hostname = parsed.hostname.lower().rstrip(".")
    if not allow_private_networks:
        if hostname in {"localhost", "localhost.localdomain"}:
            return False, "Localhost navigation is disabled by policy."
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
            return False, "Private, loopback, link-local, and reserved network targets are blocked."

    return True, "ok"
