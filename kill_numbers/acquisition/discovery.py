import base64
from kill_numbers.acquisition.policy import child_url_allowed, validate_public_request_url
import html
import re
from urllib.parse import urljoin, urlparse

from kill_numbers.text_utils import (
    unique_keep_order,
)


SCRIPT_WORKERS = 2


def decode_strdecode_payloads(value: str) -> list[str]:
    decoded = []
    patterns = [
        r"strdecode\([\"']([^\"']+)[\"']\)",
        r"decodeB64\([\"']([^\"']+)[\"']\)",
        r"__PAGE_DATA__\s*=\s*[\"']([^\"']+)[\"']",
    ]
    matches = sorted((match for pattern in patterns for match in re.finditer(pattern, value)), key=lambda m:m.start())
    for match in matches:
        try:
            decoded.append(base64.b64decode(match.group(1), validate=True).decode("utf-8"))
        except (ValueError, UnicodeError):
            continue
    return decoded


def is_fetchable_script(src: str, page_url: str) -> bool:
    parsed = urlparse(src)
    if parsed.hostname == "hm.baidu.com":
        return False
    if child_url_allowed(src, page_url):
        return True
    # These sites keep the actual post body in explicitly referenced CDN
    # /upload/script/ files.  Permit only that narrow direct-script shape;
    # arbitrary third-party libraries and XHR destinations remain blocked.
    if "/upload/script/" not in parsed.path:
        return False
    try:
        validate_public_request_url(src)
        return True
    except (OSError, ValueError):
        return False


def script_urls(html_value: str, page_url: str) -> list[str]:
    urls = []
    for match in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", html_value, re.I):
        src = html.unescape(match.group(1))
        absolute = urljoin(page_url, src)
        if is_fetchable_script(absolute, page_url):
            urls.append(absolute)
    return unique_keep_order(urls)


def iframe_urls(html_value: str, page_url: str) -> list[str]:
    urls = []
    for match in re.finditer(r"<iframe[^>]+src=[\"']([^\"']+)[\"']", html_value, re.I):
        src = html.unescape(match.group(1)).strip()
        if src and not src.lower().startswith(("javascript:", "data:")):
            absolute = urljoin(page_url, src)
            if child_url_allowed(absolute, page_url):
                urls.append(absolute)
    return unique_keep_order(urls)
