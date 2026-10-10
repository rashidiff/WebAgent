from __future__ import annotations

import ipaddress
import fnmatch
import re
from urllib.parse import urljoin, urlparse


BLOCKED_SCHEMES = {"javascript", "data", "file", "blob", "about", "chrome", "chrome-extension"}
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_TOKEN_PATTERN = re.compile(r"\b(?:sk|pk|AIza|ghp|xox[baprs])[-_A-Za-z0-9]{16,}\b|\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE)


def is_allowed_websocket_origin(origin: str | None, allowed_patterns: list[str]) -> bool:
    """Allow only explicitly configured browser extension origins."""
    if not origin:
        return False
    return any(fnmatch.fnmatchcase(origin, pattern) for pattern in allowed_patterns)


def redact_for_llm(value: object) -> str:
    """Remove common secrets and PII before browser data reaches an LLM provider."""
    text = str(value or "")
    text = _EMAIL_PATTERN.sub("[redacted-email]", text)
    text = _CARD_PATTERN.sub("[redacted-card]", text)
    return _TOKEN_PATTERN.sub("[redacted-token]", text)


def redact_url_for_llm(url: object) -> str:
    """Query strings commonly contain session IDs, search terms, and opaque tokens."""
    return redact_for_llm(url).split("?", 1)[0]


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
