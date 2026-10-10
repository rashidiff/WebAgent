from __future__ import annotations

import ipaddress
import fnmatch
from urllib.parse import urljoin, urlparse


BLOCKED_SCHEMES = {"javascript", "data", "file", "blob", "about", "chrome", "chrome-extension"}


def is_allowed_websocket_origin(origin: str | None, allowed_patterns: list[str]) -> bool:
    """Allow only explicitly configured browser extension origins."""
    if not origin:
        return False
    return any(fnmatch.fnmatchcase(origin, pattern) for pattern in allowed_patterns)


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


def validate_element_navigation(
    element: dict[str, object],
    page_url: str,
    allow_private_networks: bool = False,
) -> tuple[bool, str]:
    """Apply navigation policy to links and form controls, not only navigate()."""
    raw_target = str(element.get("href") or element.get("formAction") or "").strip()
    if not raw_target:
        return True, "ok"
    target = urljoin(page_url, raw_target) if page_url else raw_target
    return validate_navigation_url(target, allow_private_networks)
