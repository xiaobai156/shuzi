import base64
from kill_numbers.acquisition.policy import child_url_allowed
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
    return child_url_allowed(src, page_url) and urlparse(src).hostname != "hm.baidu.com"


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
